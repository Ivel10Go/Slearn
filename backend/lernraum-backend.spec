# -*- mode: python ; coding: utf-8 -*-
# PyInstaller-Bauplan fuer das Backend der Desktop-App.
#
# Gebaut wird ein Ordner (`onedir`), keine Einzeldatei: eine Einzeldatei
# entpackt sich bei jedem Start erst in ein Temp-Verzeichnis - das kostet
# Sekunden und hinterlaesst nach einem Absturz Reste. Aufruf ueber
# `desktop/scripts/build-backend.mjs`, Ergebnis in `desktop/resources/backend`.
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = (
    # `app.main` importiert einige Router erst in `create_app()`, und Uvicorn
    # waehlt Protokoll und Schleife zur Laufzeit per Namen.
    collect_submodules("app")
    + collect_submodules("uvicorn")
    # Die Migrationen werden von Alembic per Pfad geladen, nicht importiert -
    # was sie brauchen, sieht die Analyse deshalb nicht von selbst.
    + collect_submodules("alembic")
    + collect_submodules("sqlalchemy.dialects")
    + ["pgvector.sqlalchemy", "argon2", "python_multipart"]
)

a = Analysis(
    ["desktop_main.py"],
    pathex=["."],
    # Die Migrationen liegen im Bundle unter `migrations/`, nicht unter
    # `alembic/` - ein Datenordner mit dem Namen des Python-Pakets waere eine
    # Verwechslung, die nur beim Import auffaellt.
    datas=[("alembic/env.py", "migrations"), ("alembic/versions", "migrations/versions")],
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "mypy", "ruff", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="lernraum-backend",
    console=True,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, upx=False, name="backend")
