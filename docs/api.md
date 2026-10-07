# API-Referenz

Alle Routen des Backends und der Weg über den Frontend-Proxy. Die interaktive Fassung liefert das laufende Backend unter `/docs`.

## Frontend-Proxy

Der Browser ruft ausschließlich den same-origin-Pfad `/api/backend/...` auf. Der Next.js-Server verwendet den Route-Handler `app/api/backend/[...path]/route.ts`:

```text
/api/backend/:path* -> http://backend:8000/api/:path*
```

`BACKEND_INTERNAL_URL` wird nur an den Next.js-Service übergeben. Es darf kein `NEXT_PUBLIC_`-Präfix erhalten und wird nicht an Backend oder Worker weitergereicht.

Für einen nativen `npm run dev`-Prozess muss der Next.js-Server stattdessen auf das Host-Backend zeigen. PowerShell:

```powershell
$env:BACKEND_INTERNAL_URL = "http://localhost:8000"
npm run dev
```

Linux und macOS:

```bash
BACKEND_INTERNAL_URL=http://localhost:8000 npm run dev
```

## API-Vertrag

Die nichtöffentliche Backend-Basis lautet `http://backend:8000`. Über das Frontend entspricht `/api/backend/<pfad>` dem Backend-Pfad `/api/<pfad>`.

| Bereich | Methode und Pfad | Zweck |
| --- | --- | --- |
| Health | `GET /health` | Gesamtstatus einschließlich Datenbank; bei Fehlern HTTP 503 |
| Liveness | `GET /health/live` | Prozess-Liveness ohne Datenbankabfrage |
| Auth | `POST /api/v1/auth/login` | Anmeldung und Setzen des Session-Cookies |
| Auth | `POST /api/v1/auth/logout` | Session-Cookie löschen |
| Auth | `GET /api/v1/auth/me` | Aktuellen Benutzer lesen |
| Dashboard | `GET /api/v1/dashboard` | Aggregierte Lern-, Hausaufgaben-, Klausur- und Kalenderdaten |
| Stundenplan | `GET /api/v1/lessons` | Stunden für einen Zeitraum; optionale `start`/`end` als `YYYY-MM-DD`, beide inklusiv, in der Zeitzone des Benutzers, maximal 62 Tage. Liefert zusätzlich `subject_short_name` (Fachkürzel aus dem Schulportal) und `is_cancelled` (ausgefallen) |
| Willkommenssatz | `GET /api/v1/greeting` | Tagesbriefing aus Stundenplan, Kalender und offenen Aufgaben. Wird vom KI-Provider erzeugt und pro Tag gehalten; ohne Provider kommt ein Satz aus den Daten (`source: "fallback"`) |
| Willkommenssatz | `POST /api/v1/greeting/refresh` | Satz neu formulieren; kostet einen Modellaufruf |
| Fächer | `GET, POST /api/v1/subjects` | Liste und Anlage |
| Fach | `GET, PATCH, DELETE /api/v1/subjects/{subject_id}` | Einzelnes Fach lesen, ändern oder löschen |
| Hausaufgaben | `GET, POST /api/v1/homework` | Liste mit `completed`-Filter und Anlage |
| Hausaufgabe | `GET, PATCH, DELETE /api/v1/homework/{homework_id}` | Einzelne Hausaufgabe verwalten |
| Hausaufgabe | `POST /api/v1/homework/{homework_id}/complete` | Als erledigt markieren |
| Hausaufgabe | `POST /api/v1/homework/{homework_id}/toggle` | Erledigtstatus umschalten, wenn kein Body gesendet wird |
| Hausaufgabe | `PATCH /api/v1/homework/{homework_id}/completed` | Expliziten Erledigtstatus setzen |
| Klausuren | `GET, POST /api/v1/exams` | Liste mit `upcoming`-Filter und Anlage |
| Klausur | `GET, PATCH, DELETE /api/v1/exams/{exam_id}` | Einzelne Klausur verwalten |
| Kalender | `GET, POST /api/v1/calendar` | Bevorstehende Ereignisse listen oder anlegen |
| Kalender | `GET, POST /api/v1/calendar/events` | Alias der Kalenderrouten |
| Kalenderereignis | `GET, PATCH, DELETE /api/v1/calendar/events/{event_id}` | Einzelnes Ereignis verwalten |
| Datei | `POST /api/v1/files` | Datei als `multipart/form-data` hochladen; Ablage im Dokumentenverzeichnis unter UUID-Namen, SHA-256-Prüfsumme und sofortige Indizierung in Abschnitte. Mit KI-Provider kostet das Embeddings, ohne Provider bleibt der Text durchsuchbar. Nicht lesbare Dateien werden mit `status: "error"` gespeichert statt den Upload zu verweigern |
| Dokumente | `GET /api/v1/documents` | Eigene Dokumente, neueste zuerst; jeder Eintrag trägt `status` (`indexed`, `error`, beim Ordner-Sync auch `indexing`), `source` und `processed_at` |
| Dokumentsuche | `POST /api/v1/documents/search` | Abschnitte der eigenen Dokumente suchen. Die Stichwortsuche liefert immer Treffer; nur mit Provider und PostgreSQL kommen Vektortreffer hinzu, was einen Embedding-Aufruf kostet |
| Dokumentenfrage | `POST /api/v1/documents/ask` | Frage mit Quellenangaben beantworten; kostet einen Modellaufruf. `source` ist `ai` hinter einem echten Modell, `fallback` bei fehlendem Provider oder Modellfehler und `none` ohne jeden Treffer |
| Lernaufgabe | `GET, POST /api/v1/study/tasks` | Aufgaben listen oder anlegen; Liste unterstützt `status`, `study_plan_id` und `order` (`priority`, `due`, `position`) |
| Lernaufgabe | `GET, PATCH, DELETE /api/v1/study/tasks/{task_id}` | Einzelne Lernaufgabe verwalten, inklusive Priorität und Fälligkeit |
| Lernsession | `POST /api/v1/study/tasks/{task_id}/start` | Lernsession starten; idempotent für denselben Benutzer und dieselbe Aufgabe. Eine blockierte Aufgabe antwortet mit 409 |
| Lernsession | `POST /api/v1/study/tasks/{task_id}/stop` | Offene Lernsession beenden und gelernte Sekunden speichern |
| Lernsession | `POST /api/v1/study/tasks/{task_id}/complete` | Aufgabe als erledigt markieren und offene Session abschließen; erzeugt bei einer Serie die nächste Aufgabe |
| Voraussetzung | `GET, POST /api/v1/study/tasks/{task_id}/dependencies` | Voraussetzungen einer Aufgabe lesen oder `depends_on_task_id` hinzufügen. Zyklen und plangrenzüberschreitende Kanten ergeben 422 |
| Voraussetzung | `DELETE /api/v1/study/tasks/{task_id}/dependencies/{depends_on_task_id}` | Verknüpfung auflösen, damit die wartende Aufgabe wieder startbar wird |
| Wochenplan | `POST /api/v1/study/planner/preview` | Vorschlag: offene Aufgaben, Hausaufgaben und Klausurthemen auf freie Zeit neben Unterricht und Terminen verteilen. Schreibt nichts |
| Wochenplan | `POST /api/v1/study/planner/apply` | Denselben Vorschlag neu rechnen und eintragen: Termine setzen, neue Blöcke im Plan „Wochenplan“ anlegen. Idempotent |
| Lernplan | `GET, POST /api/v1/study/plans` | Lernpläne listen oder anlegen; die Liste führt `task_count` und `open_task_count` mit |
| Lernplan | `GET, PATCH, DELETE /api/v1/study/plans/{plan_id}` | Einzelplan verwalten; das Löschen nimmt Aufgaben, Serien, Sessions und Abhängigkeiten mit |
| Serie | `GET, POST /api/v1/study/series` | Wiederkehrende Aufgaben listen oder anlegen; die erste Occurrence entsteht sofort, jede weitere erst beim Abschluss der vorigen |
| Serie | `GET, PATCH, DELETE /api/v1/study/series/{series_id}` | Vorlage ändern (wirkt erst auf künftige Aufgaben) oder Serie beenden und offene Aufgaben stornieren |
| Klausurvorbereitung | `POST /api/v1/exams/{exam_id}/tasks` | Aus den Klausurthemen Aufgaben in einem Lernplan erzeugen, nach Lernstand sortiert und verkettet. Ein zweiter Lauf legt nichts doppelt an |
| Lernstand | `GET /api/v1/progress/topics` | Konfidenz je Thema mit Label, Intervall und nächstem Wiederholungstermin |
| Lernstand | `PUT /api/v1/progress/topics/{topic_id}` | Konfidenz und Wiederholungsdatum setzen; legt die Zeile bei Bedarf an, daher PUT |
| Lernprotokoll | `GET /api/v1/progress/insights` | Serie, Wochenziel, Lernzeit je Fach und Wochentagsverteilung, alle in der Zeitzone des Benutzers |
| Wochenziel | `PATCH /api/v1/progress/goal` | Wochenziel in Minuten setzen; ohne Teilung auf die vergangenen Wochentage |
| Noten | `GET, POST /api/v1/grades` | Noten listen oder anlegen |
| Note | `GET, PATCH, DELETE /api/v1/grades/{grade_id}` | Einzelne Note verwalten |
| Notenschnitt | `GET /api/v1/grades/summary` | Schnitt je Fach und gesamt, Verlauf und Hochrechnung bestanden/nicht bestanden |
| Notenfreigabe | `GET, PUT /api/v1/grades/sharing` | Ob Noten an den KI-Dienst gehen dürfen; **Standard ist aus**, ein unlesbarer Zustand bedeutet ebenfalls aus |
| Notenimport | `GET, POST /api/v1/grades/import` | Zeilen aus einem abgegebenen Dokument als Vorschau lesen und bestätigt übernehmen; zweiseitig, damit die Oberfläche keine Note erfinden kann |
| Integrationen | `GET /api/v1/integrations` | Eigene Integrationen inklusive Status und `last_sync_at` |
| Integration | `POST /api/v1/integrations` | Anlegen aus dem Katalog; Zugangsdaten werden verschlüsselt gespeichert |
| Integration | `PATCH /api/v1/integrations/{integration_id}` | Name, Config, Zugangsdaten oder `enabled` ändern |
| Integration | `DELETE /api/v1/integrations/{integration_id}` | Integration samt importierten Daten entfernen |
| Integrationstest | `POST /api/v1/integrations/{integration_id}/test` | Echte Verbindung prüfen; `missing_fields` → `enabled` → Verbindungstest |
| Integrationstest | `GET /api/v1/integrations/{integration_id}/test` | Letztes Testergebnis lesen, ohne erneut zu testen |
| Integrationskatalog | `GET /api/v1/integrations/catalog` | Kinds, Pflichtfelder und Feldbeschreibungen; ohne Session und ohne Schlüssel lesbar |
| Datensync | `POST /api/v1/integrations/{integration_id}/sync` | Daten importieren; aktuell `webuntis`, siehe unten |
| Backup | `GET /api/v1/backups/status` | Stand des Backup-Runners aus der Worker-Statusdatei; rein lesend, ohne Auslöser. HTTP 200 auch dann, wenn die Datei fehlt - `available: false` und ein deutscher Klartext sagen dann, woran es liegt |


Alle `/api/v1`-Routen außer Health erfordern standardmäßig eine gültige Session und isolieren Daten nach Benutzer. Die interaktive OpenAPI-Oberfläche ist unter `/docs`, die ReDoc-Oberfläche unter `/redoc` verfügbar.

### Stundenplan-Import aus WebUntis

`POST /api/v1/integrations/{integration_id}/sync` holt den Stundenplan direkt beim Schulportal und schreibt ihn in die Datenbank. Die Einbindung in den Einstellungen unter dem Namen **Stundenplan aktualisieren**; das Ergebnis erscheint danach im Dashboard. Es gibt keinen Request-Body und keinen Zeitraum-Parameter, der Import umfasst immer vier Kalenderwochen.

Die Reihenfolge ist dieselbe wie beim Verbindungstest: fehlende Felder → deaktivierte Integration → Schlüssel `INTEGRATION_ENCRYPTION_KEY` → echter Abruf. Ein logisch fehlgeschlagener Import antwortet wie `/test` mit HTTP 200, `ok: false` und deutschem `message`; der Fehlertext landet zusätzlich in `last_error` der Integration. Nur HTTP-Statusfehler des Stapels selbst (fehlende Session, fremde Integration, fehlender Schlüssel) ergeben 4xx bzw. 503.

Antwort zusätzlich zu den Feldern des Verbindungstests: `imported_lessons`, `subjects`, `teachers`, `rooms`, `skipped_entries`, `window_start`, `window_end` und `last_sync_at`.

Intern nutzt der Adapter die JSON-RPC-Methode `getTimetable` des Schulportals, aufgerufen über `{base_url}/WebUntis/jsonrpc.do?school={kuerzel}`. Die Parameter werden als **Objekt** übergeben, nicht als positionelles Array:

```json
{
  "id": 0,
  "method": "getTimetable",
  "params": {
    "options": {
      "id": "<Unixzeit in Millisekunden>",
      "element": { "id": "<personId>", "type": 5 },
      "type": 5,
      "startDate": "20260921",
      "endDate": "20261018",
      "showCanceledLessons": false,
      "showLessonType": false,
      "showRegistration": false,
      "showExams": false,
      "showSubstitutes": false,
      "showAdditionalInfo": false,
      "showRoomInfo": true,
      "showTeacherInfo": true,
      "showStudentgroup": true,
      "showLsText": true,
      "showLsNumber": true,
      "showSubstText": true,
      "showInfo": true,
      "showBooking": true,
      "classFields": ["id", "name", "longname", "externalkey"],
      "roomFields": ["id", "name", "longname", "externalkey"],
      "subjectFields": ["id", "name", "longname", "externalkey"],
      "teacherFields": ["id", "name", "longname", "externalkey"]
    }
  }
}
```

`element.type` ist die Element-ID der angemeldeten Person und stammt aus `personId`/`personType` der Login-Antwort: Schüler `5`, Klasse `1`, Lehrer `2`. `startDate` und `endDate` sind `YYYYMMDD` ohne Trennzeichen. WebUntis-Zeiten sind Ortszeit des Schulstandorts; der Import interpretiert sie in der Zeitzone des Benutzers und speichert sie als UTC.

Die Antwort ist ein Objekt mit `id` auf School-Ebene, `klasse`/`teachers`/`rooms`/`subjects` als Arrays von Stammdaten und `timeGrid` als Array der Unterrichtseinheiten. Eine Einheit liefert `id`, `date` (`YYYYMMDD`), `startTime`/`endTime` (`HHMM`), die Relationsarrays `kl`/`te`/`su`/`ro` mit Objekten aus den Stammdaten, `lsnumber`, `activityType` sowie optional `lstext` und `code`. Anlagen ohne `id`, `date`, `startTime` oder `endTime` werden übersprungen und in `skipped_entries` gezählt, statt den ganzen Import abbrechen zu lassen.

Fächer und Lehrer übernehmen `longname`, Räume bevorzugt `name`, weil `longname` dort teils Klassen- oder Gebäudenamen enthält. Existiert eine Stammdateneintragung schon manuell, wird sie anhand des Namens wiederverwendet, damit Fächer nicht doppelt auftauchen. Der Import ersetzt ausschließlich Lessons derselben `sync_source_id` innerhalb des Vier-Wochen-Fensters; manuell angelegte Stunden und Daten außerhalb des Fensters bleiben erhalten. `code` und `lsnumber` werden bewusst nicht gespeichert, weil die Modelle keine Felder dafür vorsehen.

Automatisch ausgelöst wird der Import über `app/services/integration_scheduler.py`: Der Scheduler läuft als Thread im Backend-Lifespan, ist über `WEBUNTIS_SYNC_ENABLED` einschaltbar und beachtet `last_sync_at`. Der Knopf in den Einstellungen bleibt der manuelle Weg und läuft auch dann, wenn der geplante Abruf heute schon stattgefunden hat.
