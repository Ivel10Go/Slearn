"""Unterrichtsmaterial aus einem Cloud-Ordner per WebDAV (Nextcloud, ownCloud, IServ ...).

Der Abruf spiegelt den entfernten Ordner nach ``<DOCUMENT_STORAGE_PATH>/webdav/<id>/``
und reicht den Spiegel an ``sync_documents`` weiter - Einlesen, Einordnen und
Entfernen geloeschter Dateien gibt es dort schon, ein zweiter Weg waere eine
zweite Stelle zum Pflegen. Geladen wird nur, was sich laut ETag geaendert hat;
die ETags stehen in ``webdav/<id>.json`` neben dem Spiegel, nicht darin (sonst
zaehlte der Ordner-Import die Datei mit).

Nur lesend. Entfernte Pfade werden auf den Basisordner begrenzt: ein ``..`` oder
ein Verweis ausserhalb waere sonst ein Schreibzugriff ausserhalb des Spiegels.
"""

from __future__ import annotations

import json
import logging
import shutil
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from urllib.parse import quote, unquote, urlsplit
from xml.etree import ElementTree

import httpx
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.entities import SyncSource
from app.services.documents import SUPPORTED_EXTENSIONS, DocumentSyncOutcome, sync_documents

logger = logging.getLogger("school_assistant.webdav")

MAX_FILES: Final = 2000
MAX_DEPTH: Final = 12
MAX_FILE_BYTES: Final = 50 * 1024 * 1024
#: Wie oft der planmaessige Abruf einen Cloud-Ordner holt. Konstante statt Einstellung (shortcut:
#: eigene Env-Variable erst, wenn jemand ein anderes Intervall braucht).
SYNC_INTERVAL_SECONDS: Final = 60 * 60

_PROPFIND_BODY: Final = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/><d:getetag/>'
    "<d:getcontentlength/><d:getlastmodified/></d:prop></d:propfind>"
)
_DAV: Final = "{DAV:}"


class WebDavError(RuntimeError):
    """Cloud-Ordner nicht erreichbar oder Anmeldung abgelehnt. Deutsche Meldung."""


@dataclass(frozen=True, slots=True)
class RemoteFile:
    rel_path: str
    version: str
    size: int | None


def _base_path(base_url: str) -> str:
    path = unquote(urlsplit(base_url).path)
    return path if path.endswith("/") else f"{path}/"


def _safe_rel(rel: str) -> str | None:
    parts = [part for part in rel.split("/") if part]
    if any(part in {".", ".."} or "\\" in part for part in parts):
        return None
    return "/".join(parts)


class WebDavClient:
    def __init__(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        timeout_seconds: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        url = base_url.strip()
        if not url.startswith(("https://", "http://")):
            raise WebDavError("Die Adresse des Cloud-Ordners muss mit https:// beginnen.")
        self._base_url = url if url.endswith("/") else f"{url}/"
        self._base_path = _base_path(self._base_url)
        self._client = httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=True,
            auth=httpx.BasicAuth(username.strip(), password),
            transport=transport,
        )

    def __enter__(self) -> WebDavClient:
        return self

    def __exit__(self, *_: object) -> None:
        self._client.close()

    def _url(self, rel: str) -> str:
        return self._base_url + quote(rel)

    def _propfind(self, rel: str, depth: str) -> list[tuple[str, bool, str, int | None]]:
        try:
            response = self._client.request(
                "PROPFIND",
                self._url(rel),
                content=_PROPFIND_BODY,
                headers={"Depth": depth, "Content-Type": "application/xml"},
            )
        except httpx.HTTPError as exc:
            raise WebDavError(f"Der Cloud-Ordner ist nicht erreichbar: {exc}") from exc
        if response.status_code in (401, 403):
            raise WebDavError("Der Cloud-Ordner lehnt die Anmeldung ab. Benutzername oder App-Passwort prüfen.")
        if response.status_code == 404:
            raise WebDavError("Diesen Ordner gibt es auf dem Server nicht. Die Adresse prüfen.")
        if response.status_code != 207:
            raise WebDavError(f"Der Server antwortet nicht wie ein WebDAV-Ordner (HTTP {response.status_code}).")
        try:
            root = ElementTree.fromstring(response.content)
        except ElementTree.ParseError as exc:
            raise WebDavError("Der Server hat keine lesbare WebDAV-Antwort geliefert.") from exc
        entries: list[tuple[str, bool, str, int | None]] = []
        for node in root.iter(f"{_DAV}response"):
            path = unquote(urlsplit(node.findtext(f"{_DAV}href", "")).path)
            if not path.startswith(self._base_path):
                continue
            rel_path = _safe_rel(path[len(self._base_path) :])
            if rel_path is None:
                continue
            prop = node.find(f"{_DAV}propstat/{_DAV}prop")
            if prop is None:
                continue
            is_dir = prop.find(f"{_DAV}resourcetype/{_DAV}collection") is not None
            length = (prop.findtext(f"{_DAV}getcontentlength") or "").strip()
            size = int(length) if length.isdigit() else None
            version = (prop.findtext(f"{_DAV}getetag") or "").strip() or (
                f"{prop.findtext(f'{_DAV}getlastmodified') or ''}|{size}"
            )
            entries.append((rel_path, is_dir, version, size))
        return entries

    def check_connection(self) -> str:
        self._propfind("", "0")
        return "Der Cloud-Ordner ist erreichbar und die Anmeldung stimmt."

    def list_files(self) -> list[RemoteFile]:
        """Alle Dateien unter dem Basisordner, Ordner fuer Ordner (``Depth: infinity`` verbieten viele Server)."""
        files: list[RemoteFile] = []
        queue: deque[tuple[str, int]] = deque([("", 0)])
        seen = {""}
        while queue:
            folder, depth = queue.popleft()
            for rel_path, is_dir, version, size in self._propfind(f"{folder}/" if folder else "", "1"):
                if rel_path in seen:
                    continue
                seen.add(rel_path)
                if is_dir:
                    if depth < MAX_DEPTH:
                        queue.append((rel_path, depth + 1))
                elif len(files) < MAX_FILES:
                    files.append(RemoteFile(rel_path, version, size))
        return files

    def download(self, rel_path: str, target: Path) -> None:
        """Laedt in eine Zwischendatei und benennt erst am Ende um - nie eine halbe Datei im Spiegel."""
        partial = target.with_name(f"{target.name}.part")
        written = 0
        try:
            with self._client.stream("GET", self._url(rel_path)) as response:
                if response.status_code != 200:
                    raise WebDavError(f"„{rel_path}“ konnte nicht geladen werden (HTTP {response.status_code}).")
                with partial.open("wb") as handle:
                    for block in response.iter_bytes():
                        written += len(block)
                        if written > MAX_FILE_BYTES:
                            raise WebDavError(f"„{rel_path}“ ist größer als 50 MB und wird übersprungen.")
                        handle.write(block)
            partial.replace(target)
        except httpx.HTTPError as exc:
            raise WebDavError(f"„{rel_path}“ konnte nicht geladen werden: {exc}") from exc
        finally:
            partial.unlink(missing_ok=True)


def mirror(client: WebDavClient, root: Path, manifest_path: Path) -> int:
    """Bringt den lokalen Spiegel auf den Stand des Servers. Liefert die Zahl geladener Dateien."""
    try:
        known: dict[str, str] = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        known = {}
    root.mkdir(parents=True, exist_ok=True)
    resolved_root = root.resolve()
    current: dict[str, str] = {}
    loaded = 0
    for remote in client.list_files():
        if Path(remote.rel_path).suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        if remote.size is not None and remote.size > MAX_FILE_BYTES:
            continue
        target = (root / remote.rel_path).resolve()
        if not target.is_relative_to(resolved_root):
            continue
        if known.get(remote.rel_path) != remote.version or not target.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                client.download(remote.rel_path, target)
            except WebDavError as exc:
                # Eine kaputte Datei haelt den Rest nicht auf; sie wird beim naechsten Lauf erneut versucht.
                logger.warning("WebDAV-Datei uebersprungen: %s", exc)
                if target.is_file() and remote.rel_path in known:
                    current[remote.rel_path] = known[remote.rel_path]
                continue
            loaded += 1
        current[remote.rel_path] = remote.version
    for path in sorted(root.rglob("*"), reverse=True):
        rel = path.relative_to(root).as_posix()
        if path.is_file() and rel not in current:
            path.unlink()
        elif path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    manifest_path.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")
    return loaded


def sync_webdav(
    db: Session,
    source: SyncSource,
    config: dict[str, str],
    credentials: dict[str, str],
    settings: Settings,
    timeout_seconds: float,
    transport: httpx.BaseTransport | None = None,
) -> DocumentSyncOutcome:
    base = Path(settings.document_storage_path) / "webdav"
    with WebDavClient(
        base_url=config.get("base_url", ""),
        username=credentials.get("username", ""),
        password=credentials.get("password", ""),
        timeout_seconds=timeout_seconds,
        transport=transport,
    ) as client:
        mirror(client, base / str(source.id), base / f"{source.id}.json")
    return sync_documents(
        db=db, source=source, folder=str(base / str(source.id)), settings=settings, prefix=f"webdav-{source.id}/"
    )


def remove_mirror(settings: Settings, source_id: int) -> None:
    base = Path(settings.document_storage_path) / "webdav"
    shutil.rmtree(base / str(source_id), ignore_errors=True)
    (base / f"{source_id}.json").unlink(missing_ok=True)
