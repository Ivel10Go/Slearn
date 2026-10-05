"""Ablage der LoRA-Adapter: je Nutzer, je Basismodell, versioniert.

Ein Adapter passt nur zu genau dem Basismodell, auf dem er trainiert wurde.
Deshalb liegt er unter dessen Id, und das Manifest haelt die Pruefsumme der
Modelldatei fest - ein Adapter fuer ein anderes Gewicht wird nicht geladen.

    <MODEL_STORAGE_PATH>/adapters/<user_id>/<model_id>/
        active.json            {"version": 2}
        v1/adapter.gguf, v1/manifest.json
        v2/...

* **Fertig ist eine Version erst mit ``manifest.json``.** Der Trainer schreibt
  in einen eigenen Ordner; :func:`register_version` prueft ihn und benennt ihn
  in einem Schritt in ``vN`` um. Ein Absturz hinterlaesst nie eine halbe Version.
* **Anlegen heisst nicht aktivieren.** Aktiviert wird erst nach der Pruefung
  (:func:`activate`); der Vorgaenger bleibt fuer :func:`rollback` liegen.
* **Ein Modellwechsel loescht nichts.** Der neue Ordner beginnt leer, der alte
  bleibt fuer ein Zurueckwechseln.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Final

from app.services.model_catalog import MODEL_ID_PATTERN

ADAPTER_FILE: Final[str] = "adapter.gguf"
MANIFEST_FILE: Final[str] = "manifest.json"
ACTIVE_FILE: Final[str] = "active.json"
#: So viele Versionen bleiben je Basismodell; die aktive zaehlt immer mit.
KEEP_VERSIONS: Final[int] = 3

_VERSION_DIR = re.compile(r"^v([1-9][0-9]*)$")


class AdapterStoreError(ValueError):
    """Ungueltige Ablage oder Version. Die Meldung ist deutsch."""


@dataclass(frozen=True, slots=True)
class AdapterManifest:
    version: int
    base_model_id: str
    #: Pruefsumme der Modelldatei, auf der trainiert wurde.
    base_sha256: str
    dataset_fingerprint: str
    example_count: int
    #: Ergebnis auf der Pruefmenge (0-1), ``None`` = nicht geprueft.
    eval_score: float | None
    created_at: str
    #: Verlust auf der Pruefmenge mit Adapter und ohne (reines Basismodell).
    eval_loss: float | None = None
    eval_loss_base: float | None = None


def model_root(storage: Path, user_id: int, base_model_id: str) -> Path:
    if not MODEL_ID_PATTERN.match(base_model_id):
        raise AdapterStoreError(f"Ungültige Modell-Id: {base_model_id!r}")
    return storage / "adapters" / str(int(user_id)) / base_model_id


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _read_manifest(directory: Path) -> AdapterManifest | None:
    try:
        data = json.loads((directory / MANIFEST_FILE).read_text(encoding="utf-8"))
        return AdapterManifest(**data)
    except (OSError, ValueError, TypeError):
        return None


def list_versions(root: Path) -> list[AdapterManifest]:
    """Alle fertigen Versionen, aelteste zuerst. Halbe Ordner tauchen nicht auf."""
    if not root.is_dir():
        return []
    found: list[AdapterManifest] = []
    for entry in root.iterdir():
        if not entry.is_dir() or not _VERSION_DIR.match(entry.name):
            continue
        manifest = _read_manifest(entry)
        if manifest is not None and (entry / ADAPTER_FILE).is_file():
            found.append(manifest)
    return sorted(found, key=lambda m: m.version)


def register_version(
    root: Path,
    staging: Path,
    *,
    base_model_id: str,
    base_sha256: str,
    dataset_fingerprint: str,
    example_count: int,
    eval_score: float | None,
    created_at: str,
    eval_loss: float | None = None,
    eval_loss_base: float | None = None,
) -> AdapterManifest:
    """Uebernimmt einen fertig trainierten Ordner als naechste Version (nicht aktiv)."""
    if not (staging / ADAPTER_FILE).is_file():
        raise AdapterStoreError("Im Trainingsordner fehlt adapter.gguf.")
    root.mkdir(parents=True, exist_ok=True)
    existing = [int(m.group(1)) for e in root.iterdir() if (m := _VERSION_DIR.match(e.name))]
    version = max(existing, default=0) + 1
    manifest = AdapterManifest(
        version=version,
        base_model_id=base_model_id,
        base_sha256=base_sha256,
        dataset_fingerprint=dataset_fingerprint,
        example_count=example_count,
        eval_score=eval_score,
        created_at=created_at,
        eval_loss=eval_loss,
        eval_loss_base=eval_loss_base,
    )
    _write_json(staging / MANIFEST_FILE, asdict(manifest))
    os.replace(staging, root / f"v{version}")
    return manifest


def active_version(root: Path) -> int | None:
    try:
        value = json.loads((root / ACTIVE_FILE).read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError, AttributeError):
        return None
    return value if isinstance(value, int) and any(m.version == value for m in list_versions(root)) else None


def activate(root: Path, version: int) -> None:
    if not any(m.version == version for m in list_versions(root)):
        raise AdapterStoreError(f"Version {version} gibt es nicht.")
    _write_json(root / ACTIVE_FILE, {"version": version})


def deactivate(root: Path) -> None:
    """Zurueck zum reinen Basismodell; die Versionen bleiben liegen."""
    (root / ACTIVE_FILE).unlink(missing_ok=True)


def rollback(root: Path) -> int | None:
    """Aktiviert die Version vor der aktiven. Gibt es keine, laeuft das Basismodell pur."""
    current = active_version(root)
    if current is None:
        return None
    older = [m.version for m in list_versions(root) if m.version < current]
    if not older:
        deactivate(root)
        return None
    activate(root, older[-1])
    return older[-1]


def active_adapter(root: Path, base_sha256: str) -> Path | None:
    """Die Adapterdatei fuer ``llama-server --lora`` - nur, wenn sie zum Gewicht passt."""
    version = active_version(root)
    if version is None:
        return None
    manifest = _read_manifest(root / f"v{version}")
    if manifest is None or manifest.base_sha256 != base_sha256:
        return None
    return root / f"v{version}" / ADAPTER_FILE


def prune(root: Path, keep: int = KEEP_VERSIONS) -> list[int]:
    """Loescht die aeltesten Versionen ueber ``keep`` hinaus - nie die aktive."""
    current = active_version(root)
    versions = [m.version for m in list_versions(root)]
    surplus = versions[: max(len(versions) - keep, 0)]
    removed = [v for v in surplus if v != current]
    for version in removed:
        shutil.rmtree(root / f"v{version}", ignore_errors=True)
    return removed
