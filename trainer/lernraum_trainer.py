"""LoRA-Trainer fuer den Lern-Assistenten - pausierbar, mit Checkpoints.

Ein eigener Prozess, kein Backend-Code: er bekommt einen Ordner und liefert
einen Adapter. Das Backend (``app/services/training_jobs.py``) legt den Ordner
an und liest nur Dateien zurueck.

    <job>/job.json          Auftrag (Basismodell, Hyperparameter)
    <job>/data/             Datensatz aus ``training_data.write_dataset()``
    <job>/progress.json     Stand, vom Trainer geschrieben
    <job>/checkpoint/       letzter vollstaendiger Zwischenstand
    <job>/STOP              vom Backend angelegt: anhalten
    <job>/out/adapter.gguf  Ergebnis fuer ``llama-server --lora``

* **Pausieren ist der Normalfall, kein Fehler.** Zwischen zwei Optimierer-
  schritten wird ``STOP`` geprueft; dann wird gespeichert und mit Status
  ``paused`` beendet. Derselbe Aufruf setzt spaeter beim Checkpoint fort.
* **Die Reihenfolge ist eine reine Funktion von Seed und Schritt**
  (:func:`micro_order`). Deshalb muss ein Checkpoint nur die Schrittzahl
  kennen, keine Liste schon gesehener Beispiele.
* **Ein Checkpoint ist ganz oder gar nicht da.** Er entsteht in
  ``checkpoint.tmp`` und wird umbenannt; ein Absturz dazwischen faellt auf
  ``checkpoint.old`` zurueck.
* **Fragen zum Material** (``data/qa_tasks.jsonl``) erzeugt der Trainer selbst,
  vor dem Training, mit dem Basismodell: erst eine Frage zum Absatz, dann die
  Antwort im echten Prompt-Format des Assistenten. Behalten wird nur, was
  :func:`grounded` als belegt erkennt. Jedes Ergebnis steht sofort in
  ``qa_generated.jsonl`` - ein Anhalten mitten im Erzeugen verliert nichts.
* Torch und Co. werden erst in :func:`train` importiert. Die reinen Teile
  laufen in den Tests mit dem Standard-Python.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import shutil
import signal
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

JOB_FILE = "job.json"
PROGRESS_FILE = "progress.json"
STOP_FILE = "STOP"
CHECKPOINT_DIR = "checkpoint"
OUT_DIR = "out"
ADAPTER_GGUF = "adapter.gguf"

STATUS_LOADING = "loading"
STATUS_GENERATING = "generating"
STATUS_RUNNING = "running"
STATUS_PAUSED = "paused"
STATUS_CONVERTING = "converting"
STATUS_DONE = "done"
STATUS_FAILED = "failed"

#: Exit-Codes, damit das Backend auch ohne progress.json weiss, was los war.
EXIT_DONE = 0
EXIT_FAILED = 1
EXIT_PAUSED = 3

#: Hoechstens so viele Pruefbeispiele werden wirklich beantwortet - je Antwort
#: einige Sekunden, und das zweimal (mit und ohne Adapter).
EVAL_GENERATE_MAX = 20
EVAL_MAX_NEW_TOKENS = 160

QA_FILE = "qa_tasks.jsonl"
QA_DONE_FILE = "qa_generated.jsonl"
QUESTION_PLACEHOLDER = "<<FRAGE>>"
QUESTION_MAX_TOKENS = 60
ANSWER_MAX_TOKENS = 320
#: So viel vom Antwortinhalt muss im passenden Auszug stehen.
GROUNDED_MIN = 0.5
QUESTION_SYSTEM = "Du hilfst Schülerinnen und Schülern beim Lernen mit ihrem eigenen Schulmaterial."

_WORD_RE = re.compile(r"\w{4,}", re.UNICODE)

_ACTION_LINE = re.compile(r"^\s*[*`>-]*\s*AKTION\b\s*:?\s*(\{.*\})", re.MULTILINE)


@dataclass(frozen=True)
class Hyper:
    rank: int = 8
    alpha: int = 16
    dropout: float = 0.05
    learning_rate: float = 2e-4
    warmup_steps: int = 5
    epochs: int = 2
    max_seq_len: int = 2048
    grad_accum: int = 4
    seed: int = 1234
    checkpoint_every: int = 10
    target_modules: tuple[str, ...] = ("q_proj", "k_proj", "v_proj", "o_proj")


@dataclass(frozen=True)
class Job:
    directory: Path
    base_model_id: str
    hf_repo: str
    revision: str
    architecture: str
    cache_dir: Path
    hyper: Hyper = field(default_factory=Hyper)

    @property
    def data_dir(self) -> Path:
        return self.directory / "data"


def load_job(directory: Path) -> Job:
    raw = json.loads((directory / JOB_FILE).read_text(encoding="utf-8"))
    base = raw["base_model"]
    hyper_raw = dict(raw.get("hyper") or {})
    if "target_modules" in hyper_raw:
        hyper_raw["target_modules"] = tuple(hyper_raw["target_modules"])
    return Job(
        directory=directory,
        base_model_id=base["id"],
        hf_repo=base["hf_repo"],
        revision=base.get("revision") or "main",
        architecture=base["architecture"],
        cache_dir=Path(raw["cache_dir"]),
        hyper=Hyper(**hyper_raw),
    )


# --- Reine Teile -------------------------------------------------------------


def micro_order(count: int, epochs: int, seed: int) -> list[int]:
    """Die Reihenfolge aller Mikro-Batches ueber alle Epochen - nur aus Seed und Anzahl."""
    order: list[int] = []
    for epoch in range(epochs):
        indices = list(range(count))
        random.Random(seed * 1000 + epoch).shuffle(indices)
        order.extend(indices)
    return order


def total_steps(count: int, epochs: int, grad_accum: int) -> int:
    return math.ceil(count * epochs / grad_accum) if count else 0


def learning_rate_at(step: int, hyper: Hyper) -> float:
    """Konstant nach kurzem Anlauf - zustandslos, also nach dem Fortsetzen derselbe Wert."""
    if hyper.warmup_steps <= 0 or step >= hyper.warmup_steps:
        return hyper.learning_rate
    return hyper.learning_rate * (step + 1) / hyper.warmup_steps


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json_atomic(path: Path, value: Any) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def read_progress(directory: Path) -> dict[str, Any]:
    try:
        value = json.loads((directory / PROGRESS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def write_progress(directory: Path, **fields: Any) -> dict[str, Any]:
    progress = {**read_progress(directory), **fields, "updated_at": now_iso()}
    write_json_atomic(directory / PROGRESS_FILE, progress)
    return progress


def commit_checkpoint(directory: Path, staging: Path) -> Path:
    """Ersetzt ``checkpoint/`` durch ``staging`` - nie bleibt ein halber Stand zurueck."""
    final = directory / CHECKPOINT_DIR
    old = directory / (CHECKPOINT_DIR + ".old")
    if old.exists():
        shutil.rmtree(old)
    if final.exists():
        os.replace(final, old)
    os.replace(staging, final)
    shutil.rmtree(old, ignore_errors=True)
    return final


def find_checkpoint(directory: Path) -> Path | None:
    """Der letzte vollstaendige Checkpoint. ``state.json`` wird zuletzt geschrieben."""
    for name in (CHECKPOINT_DIR, CHECKPOINT_DIR + ".old"):
        candidate = directory / name
        if (candidate / "state.json").is_file():
            return candidate
    return None


def stop_requested(directory: Path) -> bool:
    return (directory / STOP_FILE).exists()


def aktionen(text: str) -> list[tuple[str, str]]:
    """Art und Datum jeder ``AKTION``-Zeile - das, was ein kleines Modell am haeufigsten verrechnet."""
    found: list[tuple[str, str]] = []
    for match in _ACTION_LINE.finditer(text):
        try:
            value = json.loads(match.group(1))
        except ValueError:
            continue
        if isinstance(value, dict):
            found.append((str(value.get("typ", "")).strip().lower(), str(value.get("datum", "")).strip()))
    return found


def action_score(pairs: list[tuple[str, str]]) -> float | None:
    """Anteil der Antworten, deren Aktionen (Art, Datum) genau dem Ziel entsprechen."""
    if not pairs:
        return None
    return round(sum(aktionen(target) == aktionen(output) for target, output in pairs) / len(pairs), 4)


def action_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pruefbeispiele, deren Ziel eine Aktion enthaelt - nur dort gibt es etwas nachzurechnen."""
    return [row for row in rows if aktionen(row["messages"][-1]["content"])][:EVAL_GENERATE_MAX]


def content_words(text: str) -> set[str]:
    return {word.casefold() for word in _WORD_RE.findall(text)}


def grounded(answer: str, source: str, distractors: list[str]) -> bool:
    """Steht die Antwort im passenden Auszug - und dort mehr als in jedem unpassenden?

    Gemessen an den Inhaltswoertern (ab vier Buchstaben). Eine Antwort, die
    sich eher aus einem Ablenker speist oder nur Allgemeines sagt, lehrt das
    Falsche: entweder Verwechseln oder Erfinden.
    """
    words = content_words(answer)
    if len(words) < 3:
        return False
    share = len(words & content_words(source)) / len(words)
    others = [len(words & content_words(other)) / len(words) for other in distractors]
    return share >= GROUNDED_MIN and all(share > other for other in others)


def question_messages(task: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": QUESTION_SYSTEM},
        {
            "role": "user",
            "content": (
                f"Abschnitt aus dem Schulmaterial ({task.get('label') or 'Material'}):\n{task['content']}\n\n"
                "Formuliere genau eine Frage, die eine Schülerin oder ein Schüler zu diesem Abschnitt stellen würde "
                "und die der Abschnitt beantwortet. Gib nur die Frage aus."
            ),
        },
    ]


def clean_question(text: str) -> str | None:
    """Die erste Zeile, ohne "Frage:" und Anfuehrungszeichen - nur, wenn es wirklich eine Frage ist."""
    for line in text.strip().splitlines():
        line = re.sub(r"^\s*(frage\s*:|\d+[.)]|[-*])\s*", "", line, flags=re.IGNORECASE).strip(" \"'„“”*")
        if line:
            return line if line.endswith("?") and 10 <= len(line) <= 220 else None
    return None


def read_done(directory: Path) -> dict[str, dict[str, Any]]:
    return {row["task_id"]: row for row in read_rows(directory / QA_DONE_FILE)}


def append_done(directory: Path, row: dict[str, Any]) -> None:
    with (directory / QA_DONE_FILE).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# --- Training ----------------------------------------------------------------


class _StopFlag:
    """Strg+C oder ein Signal setzt nur die Flagge; angehalten wird am Schrittende."""

    def __init__(self) -> None:
        self.raised = False

    def __call__(self, *_: object) -> None:
        self.raised = True


def train(job: Job) -> int:  # noqa: C901 - ein Ablauf, bewusst an einem Stueck lesbar
    import torch
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from peft.utils import get_peft_model_state_dict, set_peft_model_state_dict
    from safetensors.torch import load_file, save_file
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

    from lora_gguf import convert_peft_to_gguf

    hyper = job.hyper
    flag = _StopFlag()
    signal.signal(signal.SIGINT, flag)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, flag)

    write_progress(job.directory, status=STATUS_LOADING, error=None)
    if not torch.cuda.is_available():
        raise RuntimeError("Keine CUDA-Grafikkarte gefunden - Training läuft hier nur auf NVIDIA.")

    tokenizer = AutoTokenizer.from_pretrained(job.hf_repo, revision=job.revision, cache_dir=job.cache_dir)

    def ids_of(value: Any) -> list[int]:
        # transformers 5 liefert ein BatchEncoding, aeltere Versionen eine Liste.
        return list(value["input_ids"] if hasattr(value, "keys") else value)

    def prompt_ids_of(messages: list[dict[str, str]]) -> list[int]:
        return ids_of(tokenizer.apply_chat_template(messages[:-1], add_generation_prompt=True, tokenize=True))

    def encode(row: dict[str, Any]) -> tuple[list[int], list[int]] | None:
        messages = row["messages"]
        prompt_ids = prompt_ids_of(messages)
        full_ids = ids_of(tokenizer.apply_chat_template(messages, tokenize=True))
        if full_ids[: len(prompt_ids)] != prompt_ids or len(full_ids) > hyper.max_seq_len:
            return None
        labels = [-100] * len(prompt_ids) + full_ids[len(prompt_ids) :]
        return full_ids, labels

    def encode_all(rows: list[dict[str, Any]]) -> tuple[list[tuple[list[int], list[int]]], int]:
        encoded = [encode(row) for row in rows]
        kept = [item for item in encoded if item is not None]
        return kept, len(encoded) - len(kept)

    train_rows = read_rows(job.data_dir / "train.jsonl")
    eval_rows = read_rows(job.data_dir / "eval.jsonl")
    eval_actions = action_rows(eval_rows)
    qa_tasks = read_rows(job.data_dir / QA_FILE)

    quant = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True,
    )
    model = AutoModelForCausalLM.from_pretrained(
        job.hf_repo,
        revision=job.revision,
        cache_dir=job.cache_dir,
        quantization_config=quant,
        dtype=torch.bfloat16,
        device_map={"": 0},
    )
    model = prepare_model_for_kbit_training(
        model, use_gradient_checkpointing=True, gradient_checkpointing_kwargs={"use_reentrant": False}
    )
    model = get_peft_model(
        model,
        LoraConfig(
            r=hyper.rank,
            lora_alpha=hyper.alpha,
            lora_dropout=hyper.dropout,
            target_modules=list(hyper.target_modules),
            task_type="CAUSAL_LM",
        ),
    )
    params = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(params, lr=hyper.learning_rate, weight_decay=0.0)

    def loss_of(item: tuple[list[int], list[int]]) -> Any:
        ids, labels = item
        out = model(
            input_ids=torch.tensor([ids], device="cuda"),
            labels=torch.tensor([labels], device="cuda"),
        )
        return out.loss

    def eval_loss() -> float | None:
        if not eval_items:
            return None
        model.eval()
        with torch.no_grad():
            total = sum(float(loss_of(item)) for item in eval_items)
        model.train()
        return round(total / len(eval_items), 4)

    def eval_actions_score() -> float | None:
        """Antwortet auf die Pruefbeispiele mit Aktion und vergleicht Art und Datum mit dem Ziel."""
        if not eval_actions:
            return None
        model.eval()
        pairs: list[tuple[str, str]] = []
        with torch.no_grad():
            for row in eval_actions:
                ids = prompt_ids_of(row["messages"])
                out = model.generate(
                    input_ids=torch.tensor([ids], device="cuda"),
                    attention_mask=torch.ones((1, len(ids)), dtype=torch.long, device="cuda"),
                    max_new_tokens=EVAL_MAX_NEW_TOKENS,
                    do_sample=False,
                    pad_token_id=tokenizer.eos_token_id,
                )
                text = tokenizer.decode(out[0][len(ids) :], skip_special_tokens=True)
                pairs.append((row["messages"][-1]["content"], text))
        model.train()
        return action_score(pairs)

    def generate(messages: list[dict[str, str]], max_new_tokens: int) -> str:
        ids = prompt_ids_of([*messages, {"role": "assistant", "content": ""}])
        with torch.no_grad():
            out = model.generate(
                input_ids=torch.tensor([ids], device="cuda"),
                attention_mask=torch.ones((1, len(ids)), dtype=torch.long, device="cuda"),
                max_new_tokens=max_new_tokens,
                do_sample=False,
                pad_token_id=tokenizer.eos_token_id,
            )
        return str(tokenizer.decode(out[0][len(ids) :], skip_special_tokens=True)).strip()

    # --- Fragen zum Material: vor dem Training, mit dem reinen Basismodell ---
    if qa_tasks:
        done = read_done(job.directory)
        pending = [task for task in qa_tasks if task["task_id"] not in done]
        kept = sum(1 for row in done.values() if row.get("keep"))
        if pending:
            write_progress(
                job.directory, status=STATUS_GENERATING, qa_total=len(qa_tasks), qa_done=len(done), qa_kept=kept
            )
            model.eval()
            with model.disable_adapter():
                for task in pending:
                    if flag.raised or stop_requested(job.directory):
                        write_progress(job.directory, status=STATUS_PAUSED)
                        return EXIT_PAUSED
                    raw_question = generate(question_messages(task), QUESTION_MAX_TOKENS)
                    question = clean_question(raw_question)
                    # Auch Verworfenes mit Grund ablegen - sonst laesst sich nie sagen, warum nichts blieb.
                    result: dict[str, Any] = {"task_id": task["task_id"], "keep": False, "reason": "keine_frage"}
                    result["raw_question"] = raw_question[:300]
                    if question:
                        prompt = [
                            {"role": "system", "content": task["system"]},
                            {"role": "user", "content": task["user_template"].replace(QUESTION_PLACEHOLDER, question)},
                        ]
                        answer = generate(prompt, ANSWER_MAX_TOKENS)
                        result.update(reason="nicht_belegt", question=question, answer=answer[:600])
                        if grounded(answer, task["content"], task.get("distractors", [])):
                            result.update(
                                keep=True,
                                reason="belegt",
                                eval=bool(task.get("eval")),
                                messages=[*prompt, {"role": "assistant", "content": answer}],
                            )
                            kept += 1
                    append_done(job.directory, result)
                    done[task["task_id"]] = result
                    write_progress(job.directory, qa_done=len(done), qa_kept=kept)
            model.train()
        for row in read_done(job.directory).values():
            if row.get("keep"):
                (eval_rows if row.get("eval") else train_rows).append(row)

    train_items, skipped = encode_all(train_rows)
    eval_items, _ = encode_all(eval_rows)
    if not train_items:
        raise RuntimeError("Keine Trainingsbeispiele, die in die Kontextlänge passen.")
    steps = total_steps(len(train_items), hyper.epochs, hyper.grad_accum)
    order = micro_order(len(train_items), hyper.epochs, hyper.seed)

    step = 0
    progress = read_progress(job.directory)
    checkpoint = find_checkpoint(job.directory)
    if checkpoint is not None:
        state = json.loads((checkpoint / "state.json").read_text(encoding="utf-8"))
        set_peft_model_state_dict(model, load_file(str(checkpoint / "adapter.safetensors")))
        optimizer.load_state_dict(torch.load(checkpoint / "optimizer.pt", map_location="cuda", weights_only=True))
        step = int(state["step"])
    elif "eval_loss_base" not in progress:
        # Vorher-Wert fuer die Pruefung: dasselbe Modell ohne Adapter.
        with model.disable_adapter():
            write_progress(job.directory, eval_loss_base=eval_loss(), eval_score_base=eval_actions_score())

    def save_checkpoint() -> None:
        staging = job.directory / (CHECKPOINT_DIR + ".tmp")
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir()
        save_file(get_peft_model_state_dict(model), str(staging / "adapter.safetensors"))
        torch.save(optimizer.state_dict(), staging / "optimizer.pt")
        write_json_atomic(staging / "state.json", {"step": step, "saved_at": now_iso()})
        commit_checkpoint(job.directory, staging)

    write_progress(
        job.directory,
        status=STATUS_RUNNING,
        step=step,
        total_steps=steps,
        train_examples=len(train_items),
        skipped_too_long=skipped,
    )
    model.train()
    started, started_step = time.monotonic(), step
    while step < steps:
        if flag.raised or stop_requested(job.directory):
            save_checkpoint()
            write_progress(job.directory, status=STATUS_PAUSED, step=step)
            return EXIT_PAUSED
        torch.manual_seed(hyper.seed + step)
        for group in optimizer.param_groups:
            group["lr"] = learning_rate_at(step, hyper)
        micro = order[step * hyper.grad_accum : (step + 1) * hyper.grad_accum]
        loss_sum = 0.0
        for index in micro:
            loss = loss_of(train_items[index]) / len(micro)
            loss.backward()
            loss_sum += loss.detach().item()
        torch.nn.utils.clip_grad_norm_(params, 1.0)
        optimizer.step()
        optimizer.zero_grad(set_to_none=True)
        step += 1
        if step % hyper.checkpoint_every == 0 and step < steps:
            save_checkpoint()
        write_progress(
            job.directory,
            step=step,
            loss=round(loss_sum, 4),
            seconds_per_step=round((time.monotonic() - started) / max(step - started_step, 1), 2),
        )

    write_progress(
        job.directory, status=STATUS_CONVERTING, step=step, eval_loss=eval_loss(), eval_score=eval_actions_score()
    )
    out = job.directory / OUT_DIR
    out.mkdir(exist_ok=True)
    model.save_pretrained(out / "peft")
    save_checkpoint()
    convert_peft_to_gguf(out / "peft", out / ADAPTER_GGUF, architecture=job.architecture)
    write_progress(job.directory, status=STATUS_DONE)
    return EXIT_DONE


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Slearn LoRA-Trainer")
    parser.add_argument("--job", required=True, type=Path)
    args = parser.parse_args(argv)
    directory: Path = args.job.resolve()
    (directory / STOP_FILE).unlink(missing_ok=True)
    try:
        return train(load_job(directory))
    except Exception as exc:  # noqa: BLE001 - jeder Fehler gehoert in progress.json
        write_progress(directory, status=STATUS_FAILED, error=f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
