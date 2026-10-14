"""Reicht dieser Rechner fuer das Feintuning je Nutzer?

Phase 1 trainiert nur auf leistungsstarken Rechnern; alles darunter bekommt die
Funktion gar nicht erst angeboten (statt eines Knopfs, der nach einer Stunde
scheitert). Zwei Wege gelten als tragfaehig:

* **Apple Silicon mit genug Arbeitsspeicher** - MLX trainiert dort im
  gemeinsamen Speicher von CPU und GPU.
* **NVIDIA-GPU mit genug Grafikspeicher** - PyTorch/CUDA.

Eine reine CPU, Intel-/AMD-Grafik und Intel-Macs gelten **nicht**: ein
1,5B-Modell braeuchte dort Stunden, und ein Trainingslauf, der nie fertig wird,
ist schlechter als keiner.

Zweistufig wie der Scheduler: :func:`evaluate_hardware` ist eine reine Funktion
ueber gemessenen Werten (getestet ohne echte Hardware), :func:`detect_training_hardware`
misst einmal und merkt sich das Ergebnis - die Hardware aendert sich nicht,
waehrend das Backend laeuft, und ``nvidia-smi`` kostet jedes Mal Zeit.
"""

from __future__ import annotations

import ctypes
import logging
import os
import platform
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Final

logger = logging.getLogger("school_assistant")

BACKEND_MLX: Final[str] = "mlx"
BACKEND_CUDA: Final[str] = "cuda"

#: Untergrenzen fuer Phase 1. Ein 1,5B-Modell mit LoRA braucht unter MLX rund
#: 8 GB; 16 GB lassen Platz fuer Betriebssystem, Browser und Slearn.
MIN_MAC_RAM_GB: Final[float] = 16.0
#: 6 GB VRAM reichen fuer LoRA auf einem 1,5B-Modell in 16 Bit bzw. 3B in 4 Bit.
MIN_VRAM_GB: Final[float] = 6.0
NVIDIA_SMI_TIMEOUT_SECONDS: Final[float] = 5.0

_GIB: Final[int] = 1024**3


@dataclass(frozen=True, slots=True)
class GpuInfo:
    name: str
    memory_gb: float


@dataclass(frozen=True, slots=True)
class TrainingHardware:
    eligible: bool
    backend: str | None
    reason: str
    system: str
    machine: str
    ram_gb: float | None
    gpu_name: str | None = None
    vram_gb: float | None = None


def evaluate_hardware(system: str, machine: str, ram_gb: float | None, gpus: Sequence[GpuInfo]) -> TrainingHardware:
    """Entscheidet aus gemessenen Werten. NVIDIA vor Apple, die staerkste GPU zaehlt."""
    is_apple_silicon = system == "Darwin" and machine.lower() in ("arm64", "aarch64")
    best = max(gpus, key=lambda gpu: gpu.memory_gb, default=None)

    if best is not None and best.memory_gb >= MIN_VRAM_GB:
        return TrainingHardware(
            eligible=True,
            backend=BACKEND_CUDA,
            reason=f"NVIDIA-Grafikkarte {best.name} mit {best.memory_gb:.0f} GB – Training möglich.",
            system=system,
            machine=machine,
            ram_gb=ram_gb,
            gpu_name=best.name,
            vram_gb=best.memory_gb,
        )
    if is_apple_silicon:
        if ram_gb is not None and ram_gb >= MIN_MAC_RAM_GB:
            return TrainingHardware(
                eligible=True,
                backend=BACKEND_MLX,
                reason=f"Apple Silicon mit {ram_gb:.0f} GB Arbeitsspeicher – Training möglich.",
                system=system,
                machine=machine,
                ram_gb=ram_gb,
            )
        menge = f"{ram_gb:.0f} GB" if ram_gb is not None else "unbekannt viel"
        return TrainingHardware(
            eligible=False,
            backend=None,
            reason=(
                f"Apple Silicon mit {menge} Arbeitsspeicher – fürs Training braucht es mindestens "
                f"{MIN_MAC_RAM_GB:.0f} GB."
            ),
            system=system,
            machine=machine,
            ram_gb=ram_gb,
        )
    if best is not None:
        return TrainingHardware(
            eligible=False,
            backend=None,
            reason=(
                f"Die Grafikkarte {best.name} hat {best.memory_gb:.0f} GB – fürs Training braucht es mindestens "
                f"{MIN_VRAM_GB:.0f} GB."
            ),
            system=system,
            machine=machine,
            ram_gb=ram_gb,
            gpu_name=best.name,
            vram_gb=best.memory_gb,
        )
    return TrainingHardware(
        eligible=False,
        backend=None,
        reason=(
            "Keine passende Grafikkarte gefunden. Training läuft vorerst nur auf einem Mac mit Apple-Chip "
            f"(ab {MIN_MAC_RAM_GB:.0f} GB) oder mit einer NVIDIA-Grafikkarte (ab {MIN_VRAM_GB:.0f} GB)."
        ),
        system=system,
        machine=machine,
        ram_gb=ram_gb,
    )


# --- Messen ------------------------------------------------------------------


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def total_ram_gb() -> float | None:
    """Physischer Arbeitsspeicher ohne ``psutil`` (das Bundle soll klein bleiben)."""
    try:
        if platform.system() == "Windows":
            status = _MemoryStatusEx()
            status.dwLength = ctypes.sizeof(_MemoryStatusEx)
            # getattr: ``windll`` gibt es nur unter Windows, mypy prueft aber auf jedem System.
            kernel32: Any = getattr(ctypes, "windll").kernel32
            if not kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return None
            return round(float(status.ullTotalPhys) / _GIB, 1)
        if platform.system() == "Darwin":
            result = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=2)
            return round(int(result.stdout.strip()) / _GIB, 1)
        sysconf: Any = getattr(os, "sysconf")
        return round(float(sysconf("SC_PAGE_SIZE")) * float(sysconf("SC_PHYS_PAGES")) / _GIB, 1)
    except (OSError, ValueError, AttributeError, subprocess.SubprocessError):
        logger.warning("Arbeitsspeicher konnte nicht ermittelt werden.", exc_info=True)
        return None


def parse_nvidia_smi(output: str) -> list[GpuInfo]:
    """``name, memory.total`` in MiB, eine Zeile je Karte (``--format=csv,noheader,nounits``)."""
    gpus: list[GpuInfo] = []
    for line in output.splitlines():
        name, _, memory = line.rpartition(",")
        try:
            mib = float(memory.strip())
        except ValueError:
            continue
        if name.strip():
            gpus.append(GpuInfo(name=name.strip(), memory_gb=round(mib / 1024, 1)))
    return gpus


def nvidia_gpus() -> list[GpuInfo]:
    """Ohne ``nvidia-smi`` gibt es keine nutzbare NVIDIA-Karte - der Treiber bringt es mit."""
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return []
    try:
        result = subprocess.run(
            [executable, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=NVIDIA_SMI_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        logger.warning("nvidia-smi ist nicht gelaufen.", exc_info=True)
        return []
    return parse_nvidia_smi(result.stdout) if result.returncode == 0 else []


@lru_cache(maxsize=1)
def detect_training_hardware() -> TrainingHardware:
    return evaluate_hardware(platform.system(), platform.machine(), total_ram_gb(), nvidia_gpus())
