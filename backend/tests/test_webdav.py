"""Cloud-Ordner per WebDAV: spiegeln, nur Geaendertes laden, Geloeschtes entfernen, Pfade begrenzen."""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import quote

import httpx
import pytest
from app.core.config import Settings
from app.db.entities import Document, SyncSource, User
from app.services.integration_scheduler import active_scheduled_sources
from app.services.webdav import WebDavClient, WebDavError, sync_webdav
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

BASE = "https://cloud.example/remote.php/dav/files/u/Schule/"
BASE_PATH = "/remote.php/dav/files/u/Schule/"


class FakeCloud:
    """Ein winziger WebDAV-Server: ``files`` ist rel_path -> (etag, Inhalt)."""

    def __init__(self) -> None:
        self.files: dict[str, tuple[str, bytes]] = {}
        self.extra_hrefs: list[str] = []
        self.downloads: list[str] = []
        self.status = 207

    def _entry(self, href: str, *, folder: bool, etag: str = "", size: int = 0) -> str:
        kind = "<d:collection/>" if folder else ""
        return (
            f"<d:response><d:href>{href}</d:href><d:propstat><d:prop><d:resourcetype>{kind}</d:resourcetype>"
            f"<d:getetag>{etag}</d:getetag><d:getcontentlength>{'' if folder else size}</d:getcontentlength>"
            "</d:prop></d:propstat></d:response>"
        )

    def handler(self, request: httpx.Request) -> httpx.Response:
        rel = request.url.path[len(BASE_PATH) :] if request.url.path.startswith(BASE_PATH) else None
        if rel is None:
            return httpx.Response(404)
        rel = httpx.URL(f"https://x/{rel}").path[1:]
        if request.method == "GET":
            self.downloads.append(rel)
            return httpx.Response(200, content=self.files[rel][1])
        if self.status != 207:
            return httpx.Response(self.status)
        folder = rel.rstrip("/")
        entries = [self._entry(BASE_PATH + quote(rel), folder=True)]
        children: set[str] = set()
        for path, (etag, content) in self.files.items():
            if folder and not path.startswith(f"{folder}/"):
                continue
            rest = path[len(folder) + 1 :] if folder else path
            head, _, tail = rest.partition("/")
            child = f"{folder}/{head}" if folder else head
            if tail:
                if child not in children:
                    children.add(child)
                    entries.append(self._entry(BASE_PATH + quote(child) + "/", folder=True))
            else:
                entries.append(self._entry(BASE_PATH + quote(child), folder=False, etag=etag, size=len(content)))
        if not folder:
            entries += [self._entry(href, folder=False, etag="x", size=1) for href in self.extra_hrefs]
        body = f'<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">{"".join(entries)}</d:multistatus>'
        return httpx.Response(207, content=body.encode())


def make_source(db: Session, user: User) -> SyncSource:
    source = SyncSource(user_id=user.id, source_type="webdav", name="Cloud", configuration={"base_url": BASE})
    db.add(source)
    db.commit()
    return source


def run(db: Session, source: SyncSource, settings: Settings, cloud: FakeCloud):
    return sync_webdav(
        db,
        source,
        {"base_url": BASE},
        {"username": "u", "password": "p"},
        settings,
        5,
        transport=httpx.MockTransport(cloud.handler),
    )


def test_spiegelt_ordner_und_laedt_nur_geaendertes(
    app_context: tuple[TestClient, Session, User], settings: Settings
) -> None:
    _, db, user = app_context
    source = make_source(db, user)
    cloud = FakeCloud()
    cloud.files = {
        "Mathe/Kettenregel Notizen.txt": ("e1", "Kettenregel: aeussere mal innere Ableitung".encode()),
        "Deutsch/Gedicht.md": ("e2", b"# Metrum\nJambus"),
        "Mathe/foto.png": ("e3", b"\x89PNG"),
    }

    outcome = run(db, source, settings, cloud)
    assert outcome.imported_documents == 2
    assert sorted(cloud.downloads) == ["Deutsch/Gedicht.md", "Mathe/Kettenregel Notizen.txt"]
    names = db.scalars(select(Document.external_id).where(Document.sync_source_id == source.id)).all()
    assert sorted(names) == [f"webdav-{source.id}/Deutsch/Gedicht.md", f"webdav-{source.id}/Mathe/Kettenregel Notizen.txt"]

    cloud.downloads.clear()
    assert run(db, source, settings, cloud).imported_documents == 0
    assert cloud.downloads == []

    cloud.files["Deutsch/Gedicht.md"] = ("e2b", b"# Metrum\nTrochaeus")
    del cloud.files["Mathe/Kettenregel Notizen.txt"]
    outcome = run(db, source, settings, cloud)
    assert cloud.downloads == ["Deutsch/Gedicht.md"]
    assert (outcome.updated_documents, outcome.removed_documents) == (1, 1)


def test_verweise_ausserhalb_des_ordners_werden_ignoriert(
    app_context: tuple[TestClient, Session, User], settings: Settings
) -> None:
    _, db, user = app_context
    source = make_source(db, user)
    cloud = FakeCloud()
    cloud.extra_hrefs = ["/remote.php/dav/files/u/Privat/tagebuch.txt", BASE_PATH + "../../geheim.txt"]
    assert run(db, source, settings, cloud).imported_documents == 0
    assert cloud.downloads == []


def test_abgelehnte_anmeldung_hat_klartext() -> None:
    cloud = FakeCloud()
    cloud.status = 401
    with WebDavClient(
        base_url=BASE, username="u", password="falsch", timeout_seconds=5, transport=httpx.MockTransport(cloud.handler)
    ) as client:
        with pytest.raises(WebDavError, match="App-Passwort"):
            client.check_connection()


def test_cloud_ordner_wird_planmaessig_geholt(app_context: tuple[TestClient, Session, User]) -> None:
    _, db, user = app_context
    source = make_source(db, user)
    source.last_sync_at = datetime.now(UTC)
    db.commit()
    assert [row[0].id for row in active_scheduled_sources(db)] == [source.id]
