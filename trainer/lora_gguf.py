"""PEFT-LoRA (safetensors) nach GGUF fuer ``llama-server --lora``.

Bewusst klein statt ``convert_lora_to_gguf.py`` aus dem llama.cpp-Quelltext:
der braucht das ganze Repository samt passender Version. Das Format ist
schlicht - je Gewicht zwei Tensoren ``<name>.lora_a`` (r x in) und
``<name>.lora_b`` (out x r) plus drei Schluessel. llama.cpp prueft beim Laden
die Formen gegen das Basismodell, ein Fehler hier faellt also sofort auf.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

#: Llama-artige Architekturen (llama, qwen2, mistral) benennen gleich.
MODULE_NAMES = {
    "q_proj": "attn_q",
    "k_proj": "attn_k",
    "v_proj": "attn_v",
    "o_proj": "attn_output",
    "gate_proj": "ffn_gate",
    "up_proj": "ffn_up",
    "down_proj": "ffn_down",
}

_KEY = re.compile(r"layers\.(\d+)\.(?:self_attn|mlp)\.(\w+)\.lora_([AB])\.weight$")


def gguf_name(peft_key: str) -> str | None:
    """``...layers.3.self_attn.q_proj.lora_A.weight`` -> ``blk.3.attn_q.weight.lora_a``."""
    match = _KEY.search(peft_key)
    if match is None or match.group(2) not in MODULE_NAMES:
        return None
    layer, module, part = match.groups()
    return f"blk.{layer}.{MODULE_NAMES[module]}.weight.lora_{part.lower()}"


def convert_peft_to_gguf(peft_dir: Path, target: Path, *, architecture: str) -> Path:
    import gguf
    from safetensors.torch import load_file

    config = json.loads((peft_dir / "adapter_config.json").read_text(encoding="utf-8"))
    tensors = load_file(str(peft_dir / "adapter_model.safetensors"))
    named = {}
    for key, value in tensors.items():
        name = gguf_name(key)
        if name is None:
            raise ValueError(f"Unbekannter LoRA-Tensor: {key}")
        # bf16 kennt numpy nicht; der Adapter ist klein, f32 kostet nichts.
        named[name] = value.float().cpu().numpy()
    if not named:
        raise ValueError("Der Adapter enthält keine LoRA-Tensoren.")

    temporary = target.with_name(target.name + ".tmp")
    writer = gguf.GGUFWriter(str(temporary), architecture)
    writer.add_string("general.type", "adapter")
    writer.add_string("adapter.type", "lora")
    writer.add_float32("adapter.lora.alpha", float(config["lora_alpha"]))
    for name in sorted(named):
        writer.add_tensor(name, named[name])
    writer.write_header_to_file()
    writer.write_kv_data_to_file()
    writer.write_tensors_to_file()
    writer.close()
    temporary.replace(target)
    return target
