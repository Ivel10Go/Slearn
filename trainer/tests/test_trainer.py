"""Die reinen Teile des Trainers - ohne torch, mit dem Standard-Python.

Aus dem Repo-Root: ``python -m unittest discover -s trainer/tests``.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import lernraum_trainer as t  # noqa: E402
from lora_gguf import gguf_name  # noqa: E402


class ReihenfolgeTest(unittest.TestCase):
    def test_reihenfolge_haengt_nur_an_seed_und_anzahl(self) -> None:
        erste = t.micro_order(7, 2, seed=5)
        self.assertEqual(erste, t.micro_order(7, 2, seed=5))
        self.assertNotEqual(erste, t.micro_order(7, 2, seed=6))
        # Jede Epoche sieht jedes Beispiel genau einmal.
        self.assertEqual(sorted(erste[:7]), list(range(7)))
        self.assertEqual(sorted(erste[7:]), list(range(7)))

    def test_schrittzahl_rundet_auf(self) -> None:
        self.assertEqual(t.total_steps(10, 2, 4), 5)
        self.assertEqual(t.total_steps(9, 1, 4), 3)
        self.assertEqual(t.total_steps(0, 2, 4), 0)

    def test_lernrate_ist_nach_dem_fortsetzen_dieselbe(self) -> None:
        hyper = t.Hyper(learning_rate=1e-3, warmup_steps=4)
        self.assertAlmostEqual(t.learning_rate_at(0, hyper), 2.5e-4)
        self.assertEqual(t.learning_rate_at(4, hyper), 1e-3)
        self.assertEqual(t.learning_rate_at(400, hyper), 1e-3)


class CheckpointTest(unittest.TestCase):
    def test_checkpoint_wird_ersetzt_und_der_alte_bleibt_bis_zum_schluss(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp)
            for schritt in (10, 20):
                staging = job / "checkpoint.tmp"
                staging.mkdir()
                (staging / "state.json").write_text(json.dumps({"step": schritt}), encoding="utf-8")
                t.commit_checkpoint(job, staging)
            gefunden = t.find_checkpoint(job)
            assert gefunden is not None
            self.assertEqual(json.loads((gefunden / "state.json").read_text(encoding="utf-8"))["step"], 20)
            self.assertFalse((job / "checkpoint.old").exists())

    def test_absturz_beim_umbenennen_faellt_auf_den_alten_stand(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp)
            alt = job / "checkpoint.old"
            alt.mkdir()
            (alt / "state.json").write_text('{"step": 10}', encoding="utf-8")
            # Halber neuer Stand ohne state.json.
            (job / "checkpoint").mkdir()
            self.assertEqual(t.find_checkpoint(job), alt)

    def test_ohne_checkpoint_beginnt_es_von_vorn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(t.find_checkpoint(Path(tmp)))


class AuftragTest(unittest.TestCase):
    def test_auftrag_und_fortschritt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp)
            (job / t.JOB_FILE).write_text(
                json.dumps(
                    {
                        "base_model": {"id": "m", "hf_repo": "Org/M", "revision": "abc", "architecture": "qwen2"},
                        "cache_dir": str(job / "cache"),
                        "hyper": {"rank": 4, "target_modules": ["q_proj"]},
                    }
                ),
                encoding="utf-8",
            )
            auftrag = t.load_job(job)
            self.assertEqual(auftrag.hyper.rank, 4)
            self.assertEqual(auftrag.hyper.target_modules, ("q_proj",))
            self.assertEqual(auftrag.hyper.epochs, t.Hyper().epochs)

            t.write_progress(job, status=t.STATUS_RUNNING, step=3)
            t.write_progress(job, step=4)
            stand = t.read_progress(job)
            self.assertEqual((stand["status"], stand["step"]), ("running", 4))

            self.assertFalse(t.stop_requested(job))
            (job / t.STOP_FILE).touch()
            self.assertTrue(t.stop_requested(job))


class VerhaltenspruefungTest(unittest.TestCase):
    def test_art_und_datum_zaehlen_der_text_nicht(self) -> None:
        ziel = 'Ich trage es ein.\nAKTION {"typ": "pruefung", "titel": "Bio", "datum": "2026-12-03"}'
        gleich = 'Mach ich!\n**AKTION {"typ": "Pruefung", "titel": "Biologie", "datum": "2026-12-03"}**'
        falsches_datum = 'AKTION {"typ": "pruefung", "titel": "Bio", "datum": "2026-12-10"}'
        self.assertEqual(t.action_score([(ziel, gleich)]), 1.0)
        self.assertEqual(t.action_score([(ziel, gleich), (ziel, falsches_datum), (ziel, "Keine Aktion.")]), 0.3333)
        self.assertIsNone(t.action_score([]))

    def test_kaputtes_json_ist_keine_aktion(self) -> None:
        self.assertEqual(t.aktionen('AKTION {"typ": "termin", "datum": }'), [])

    def test_nur_pruefbeispiele_mit_aktion_werden_beantwortet(self) -> None:
        def zeile(antwort: str) -> dict[str, object]:
            return {"messages": [{"role": "user", "content": "?"}, {"role": "assistant", "content": antwort}]}

        zeilen = [zeile("Nur Text.")] + [zeile('AKTION {"typ": "termin", "datum": "2026-01-01"}')] * 30
        self.assertEqual(len(t.action_rows(zeilen)), t.EVAL_GENERATE_MAX)


class MaterialFragenTest(unittest.TestCase):
    def test_nur_eine_echte_frage_wird_genommen(self) -> None:
        self.assertEqual(t.clean_question('Frage: "Was beschreibt die Ableitung?"\nZweite Zeile'), "Was beschreibt die Ableitung?")
        self.assertIsNone(t.clean_question("Die Ableitung ist die Steigung."))
        self.assertIsNone(t.clean_question("Was?"))

    def test_belegt_heisst_aus_dem_passenden_auszug_und_nicht_aus_einem_ablenker(self) -> None:
        auszug = "Ableitung: Steigung der Tangente am Graphen einer Funktion"
        ablenker = ["Die Zelle besitzt einen Zellkern und Mitochondrien"]
        self.assertTrue(t.grounded("Die Ableitung beschreibt die Steigung der Tangente am Graphen.", auszug, ablenker))
        self.assertFalse(t.grounded("Die Zelle besitzt einen Zellkern und Mitochondrien.", auszug, ablenker))
        # Allgemeines ohne Inhalt lehrt nichts.
        self.assertFalse(t.grounded("Ja, genau.", auszug, ablenker))

    def test_erzeugte_fragen_ueberleben_ein_anhalten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp)
            t.append_done(job, {"task_id": "q1", "keep": True, "messages": []})
            t.append_done(job, {"task_id": "q2", "keep": False})
            erledigt = t.read_done(job)
            self.assertEqual(set(erledigt), {"q1", "q2"})
            self.assertTrue(erledigt["q1"]["keep"])


class GgufNamenTest(unittest.TestCase):
    def test_peft_namen_werden_zu_llama_cpp_namen(self) -> None:
        self.assertEqual(
            gguf_name("base_model.model.model.layers.3.self_attn.q_proj.lora_A.weight"), "blk.3.attn_q.weight.lora_a"
        )
        self.assertEqual(
            gguf_name("base_model.model.model.layers.12.mlp.down_proj.lora_B.weight"), "blk.12.ffn_down.weight.lora_b"
        )
        self.assertIsNone(gguf_name("base_model.model.lm_head.lora_A.weight"))


if __name__ == "__main__":
    unittest.main()
