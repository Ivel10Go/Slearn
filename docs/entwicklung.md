# Entwicklung, Tests und CI

Befehle für Tests, Lint und Builds sowie die CI-Workflows. Die Fallstricke je Bereich stehen in [AGENTS.md](../AGENTS.md).

## Entwicklung mit Compose

```bash
docker compose up --build
docker compose ps
docker compose logs -f backend frontend worker
docker compose restart backend
docker compose up -d --build backend worker
docker compose down
```

`docker compose down` entfernt die Container, aber nicht `pgdata`, `worker-status` oder die Host-Verzeichnisse. `docker compose down -v` löscht beide Named Volumes und darf nur nach bestätigter Sicherung verwendet werden.

## Tests, Lint, Typecheck und Builds

### Worker

Vom Repository-Root:

```bash
python -m unittest discover -s worker/tests -v
python -m py_compile worker/main.py worker/healthcheck.py worker/env.py worker/backup.py
```

ARM64-Buildcheck des isolierten Worker-Images, manuell auf einem Host mit aktivem buildx und QEMU:

```bash
docker buildx build --platform linux/arm64 --file worker/Dockerfile --tag school-assistant-worker:arm64-check .
```

Denselben Build für alle drei Images automatisiert `.github/workflows/arm64.yml`, siehe "ARM64-Buildcheck".

### Backend

```bash
cd backend
python -m pytest
python -m ruff check app alembic tests
python -m mypy app
```

Die benötigten Entwicklungsabhängigkeiten stehen in `backend/pyproject.toml` unter `dev`.

### Frontend

```bash
cd frontend
npm test
npm run lint
npm run typecheck
npx next build --webpack
```

Für den Build ist `npx next build --webpack` zu verwenden, nicht `npm run build`. Das Script `build` ist `next build` und damit Turbopack, das reproduzierbar mit `TurbopackInternalError: ModuleGraph::from_graphs was canceled` abbricht, auch bei vollständig freiem Laufwerk. `frontend/Dockerfile` benutzt dieselbe Flag, deshalb baut die Pipeline mit derselben Option wie das Image.

Frontend und Backend benötigen für einen nativen Start ihre eigenen Abhängigkeiten. Der normale Compose-Build installiert und kapselt sie in den jeweiligen Images.

### Compose

```bash
docker compose config --quiet
```

Die vollständige Ausgabe von `docker compose config` enthält interpolierte Zugangsdaten und darf nicht geteilt oder ungeschützt protokolliert werden.

### Continuous Integration

Zwei Workflows unter `.github/workflows/`. Es gibt bewusst kein Root-`package.json` und keinen Task-Runner; die Jobs rufen genau die Befehle auf, die oben stehen, aus dem Verzeichnis, zu dem sie gehören.

`ci.yml` läuft bei jedem Pull Request, bei jedem Push auf `main` und manuell. Sieben Jobs ergeben elf Prüfungen, weil die Matrizen aus `frontend` und `backend` je dreifach laufen. Jede Prüfung steht einzeln, damit ein Fehler eindeutig zuzuordnen ist:

| Job | Befehl | Arbeitsverzeichnis |
| --- | --- | --- |
| `frontend (test)` | `npm test` | `frontend/` |
| `frontend (lint)` | `npm run lint` | `frontend/` |
| `frontend (typecheck)` | `npm run typecheck` | `frontend/` |
| `frontend build` | `npx next build --webpack` | `frontend/` |
| `backend (pytest)` | `python -m pytest` | `backend/` |
| `backend (ruff)` | `python -m ruff check app alembic tests` | `backend/` |
| `backend (mypy)` | `python -m mypy app` | `backend/` |
| `worker` | `python -m unittest discover -s worker/tests -v` | Repository-Root |
| `arm64-wheels` | `pip download` für `linux/arm64` | Repository-Root |
| `compose-config` | `docker compose config --quiet` | Repository-Root |
| `worker-image` | `docker build -f worker/Dockerfile .` | Repository-Root |

Installiert wird mit `npm ci` und `pip install -r requirements.txt -e ".[dev]"`, beides mit Cache. Für Python zählen `backend/requirements.txt` und `backend/pyproject.toml` in den Cache-Schlüssel, weil die Laufzeitabhängigkeiten im Dockerfile und die Werkzeuge in der `dev`-Extra stehen. Der Editable-Install legt auf dem Entwicklerrechner `backend/school_assistant_backend.egg-info/` an, das `.gitignore` noch nicht abdeckt; auf dem Runner stört das nicht.

Drei Details, die den Unterschied zwischen einem grünen und einem grünen Pipeline-Ergebnis ausmachen:

- `npx next build --webpack` statt `npm run build`. Das `build`-Script ist `next build` und damit Turbopack, das reproduzierbar abbricht. Das Frontend-Image benutzt dieselbe Flag, der Job baut also genau das, was das Image baut.
- Die Worker-Tests laufen aus dem Repository-Root, nicht aus `worker/`. Der Job setzt deshalb bewusst kein `working-directory` und ruft zusätzlich `python -m py_compile worker/main.py` auf, weil `worker/main.py` von keinem Test importiert wird.
- `compose-config` benutzt `--quiet`. Ohne die Flag landen die interpolierten Zugangsdaten im Log, und `.env.example` liefert absichtlich leere Werte für die fail-closed Variablen; der Job setzt dafür Wegwerfwerte als Schritt-Umgebung.

#### Die bekannten Abweichungen

`npm test` und `npm run lint` sind seit Oktober 2026 bei HEAD grün; die Liste der akzeptierten Abweichungen ist leer. Vorher waren beide rot (ein Test in `lib/types.test.ts`, 22 `@typescript-eslint/no-explicit-any`), weil Dashboard-, Lern- und Kontoschemas in `lib/schemas.ts` `z.object({}).passthrough()`-Stubs waren. Der Prüfer bleibt trotzdem im Weg, weil er abgestürzte Werkzeuge erkennt.

Die Pipeline macht daraus **kein** `continue-on-error`. Stattdessen laufen beide Werkzeuge mit einem JSON-Reporter zusätzlich zur normalen Ausgabe, und `.github/ci/check-known-failures.mjs` entscheidet über den Exit-Code des Schritts:

```bash
npm test -- --reporter=default --reporter=json --outputFile.json="$RUNNER_TEMP/vitest.json" || true
node ../.github/ci/check-known-failures.mjs frontend-test "$RUNNER_TEMP/vitest.json"
```

`|| true` bezieht sich nur auf den Exit-Code des Werkzeugs, nicht auf den Schritt. Das Skript schlägt fehl bei jedem Fehler außerhalb der Liste in `.github/ci/known-failures.mjs`, und die Liste ist enger als "Frontend-Lint auslassen":

- Ein Testname wird gebildet aus Datei, `describe` und `it`; heute ist keiner erlaubt.
- Ein Lint-Eintrag ist ein Paar aus Datei und Regel mit einer Obergrenze an Fundstellen; heute gibt es keinen.
- Fehlt ein Eintrag in der Liste, wird jeder Fund in dieser Datei und Regel zum Fehler.
- Ein fehlender, leerer oder unlesbarer Report ist ein harter Fehler. Genau diesen Fall würde ein pauschales `continue-on-error` verschlucken: ein Tool, das vor dem Schreiben des Reports stirbt, sieht sonst wie "nichts gefunden" aus.
- Ein Eintrag, der nicht mehr fehlschlägt, lässt den Build durch und erzeugt eine Warnung. Die Ausnahme soll nicht still veralten, denn eine Liste, die zu groß ist, ist wieder ein Freibrief.

Die Baseline ändert sich nur zusammen mit dem Code, der sie begründet. Muss je wieder eine Abweichung akzeptiert werden, gehört sie so eng wie möglich in `known-failures.mjs` – nicht in ein `continue-on-error`.

#### ARM64-Buildcheck

`.github/workflows/arm64.yml` baut die drei Images für `linux/arm64`. Der Job läuft **nicht** bei jedem Pull Request, sondern manuell (`workflow_dispatch`) und wöchentlich per `schedule`, mit einer Matrix über `backend`, `worker` und `frontend` und einem `timeout-minutes: 90`. Jeder Eintrag prüft am Ende `docker image inspect --format '{{.Architecture}}'`, denn ein Build, der nichts prüft, beweist nur, dass buildx mit Exit 0 zurückkam.

Der Grund für die Trennung ist die Rechenzeit. Der Runner ist amd64, jeder RUN-Schritt eines arm64-Builds läuft also unter QEMU-Emulation; `npm ci` plus `next build` unter Emulation ist der teuerste Fall der Pipeline und gehört nicht in den Weg jedes Pull Requests.

Was im Pull Request stattdessen läuft, ist `arm64-wheels` aus `ci.yml`: `pip download --only-binary=:all:` mit den Plattform-Tags `manylinux_2_28_aarch64` und `manylinux_2_17_aarch64` für `backend/requirements.txt` und `worker/requirements.txt`. Das beweist in Sekunden die Eigenschaft, an der es in der Praxis scheitert -- eine gepinnte Version ohne arm64-Wheel -- und ohne Emulation.

Zwei `--platform`-Angaben sind nötig, nicht redundant: `psycopg-binary==3.3.4` liefert nur `manylinux_2_27`/`manylinux_2_28`, `pydantic-core` nur `manylinux_2_17`, und pip akzeptiert eine Wheel pro Target-Glibc nur in eine Richtung. Mit einer einzigen Angabe scheitert jeweils eine der beiden Familien. `pgvector` ist davon nicht betroffen, das Paket ist reines Python (`py3-none-any`); architekturabhängig sind im Stack `psycopg-binary` und das Image `pgvector/pgvector:0.8.1-pg15-bookworm`.

Ein erfolgreicher arm64-Build ist trotzdem kein Raspberry-Pi-Test. Er sagt nichts über Startzeiten, Speicherverbrauch von `next build` auf einem ARM-Prozessor oder das Verhalten von pgvector auf der Zielhardware aus. Dafür wäre ein selbst gehosteter Runner auf dem Gerät nötig; einen gibt es nicht, und er ist auch nicht eingerichtet.

#### Lokal gegen die Pipeline

Die Befehle sind absichtlich einzeln, weil es keinen Task-Runner gibt. Unter Windows blockiert die Execution Policy `npm.ps1`, dort also `npm.cmd` und `npx.cmd`; auf Linux in der Pipeline genügt `npm`.

**Nicht verifiziert.** Die Workflows wurden in dieser Umgebung nicht ausgeführt: es gibt keinen Docker-Daemon, und ein GitHub-Runner stand nicht zur Verfügung. Verifiziert sind die zugrunde liegenden Befehle einzeln (`npm test`, `npm run lint`, `npm run typecheck`, `npx next build --webpack`, `python -m pytest`, `python -m ruff check`, `python -m mypy app`, die Worker-Tests, `pip download` für aarch64, `docker compose config --quiet`), das Verhalten des Baseline-Skripts gegen echte und absichtlich veränderte Reports, die Syntax aller Run-Blöcke und die Auflösung beider Workflow-Dateien. Ungeprüft bleiben die Aktionen Dritter, das Caching, die QEMU-Builds und die Matrix-Zuordnung auf echten Runnern.
