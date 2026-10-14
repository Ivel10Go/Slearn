# Slearn – Funktionen im Überblick

Slearn ist ein persönlicher Schulassistent. Er bündelt Stundenplan, Hausaufgaben, Prüfungen, Termine, Lernpläne, Noten, Lernzeit und eigene Unterlagen an einem Ort und hilft beim Lernen – auf Wunsch mit einer KI, die komplett lokal auf dem eigenen Rechner läuft.

Diese Seite erklärt, **was** die App kann und **wie** die einzelnen Teile funktionieren. Für Installation und Betrieb siehe [README](../README.de.md), für die API [api.md](api.md), für Konfiguration [konfiguration.md](konfiguration.md).

---

## Inhalt

1. [Zwei Betriebsarten](#zwei-betriebsarten)
2. [Die Seiten der App](#die-seiten-der-app)
3. [Übersicht (Dashboard)](#übersicht-dashboard)
4. [Stundenplan](#stundenplan)
5. [Hausaufgaben, Prüfungen und Termine](#hausaufgaben-prüfungen-und-termine)
6. [Lernverwaltung: Pläne, Serien, Aufgaben](#lernverwaltung-pläne-serien-aufgaben)
7. [Wochenplaner](#wochenplaner)
8. [Lernfortschritt und Wiederholungen](#lernfortschritt-und-wiederholungen)
9. [Noten](#noten)
10. [Belohnungen](#belohnungen)
11. [Dokumente und Unterrichtsmaterial](#dokumente-und-unterrichtsmaterial)
12. [Lern-Assistent](#lern-assistent)
13. [KI: lokal, online oder ohne](#ki-lokal-online-oder-ohne)
14. [Sprache: Sprechen und Vorlesen](#sprache-sprechen-und-vorlesen)
15. [Persönliches Training (LoRA)](#persönliches-training-lora)
16. [Integrationen](#integrationen)
17. [Benachrichtigungen](#benachrichtigungen)
18. [Datenschutz und Konto](#datenschutz-und-konto)
19. [Mehrsprachigkeit](#mehrsprachigkeit)
20. [Backups](#backups)
21. [Technischer Aufbau](#technischer-aufbau)

---

## Zwei Betriebsarten

| | Desktop-App | Server |
| --- | --- | --- |
| Für wen | eine Person an ihrem Rechner | selbst betrieben, z. B. Heimserver |
| Installation | Installer (Windows, macOS) | Docker Compose |
| Anmeldung | keine – Name bei der Ersteinrichtung | Benutzername und Passwort |
| Datenbank | SQLite im Benutzerordner | PostgreSQL 15 + pgvector |
| Lokale KI | eingebaut (lädt Laufzeiten selbst) | Betreiber bringt eigene mit |
| Updates | automatisch über GitHub-Releases, mit Datenbanksicherung vorher | `docker compose pull/build` |

Die Desktop-App hat zusätzlich Infobereich-Symbol, Autostart und Offline-Verhalten. Alle Prozesse laufen nur auf `127.0.0.1`.

---

## Die Seiten der App

| Pfad | Name | Zweck |
| --- | --- | --- |
| `/` | Übersicht | „Was steht heute an?“ – Stundenplan, Fälliges, Begrüßung |
| `/lernen` | Lernen | Wochenplaner, Lernpläne, Serien, Aufgaben |
| `/faecher` | Fächer | Fächer, Kürzel, Farben, Themen |
| `/dokumente` | Dokumente | Unterlagen hochladen, Material sortieren |
| `/fortschritt` | Fortschritt | Lernstand, Noten, Belohnungen – „Wie stehe ich?“ |
| `/assistent` | Assistent | Chat und Gesprächsmodus mit dem Lern-Assistenten |
| `/ki` | KI & Modelle | KI-Dienst, lokale Modelle, Stimmen, Training |
| `/settings` | Einstellungen | Konto, Integrationen, Benachrichtigungen, Sprache, Daten |
| `/offline` | – | Rückfallseite, wenn keine Verbindung besteht |

Die App ist als PWA installierbar; ein Service Worker hält die Shell offline verfügbar.

---

## Übersicht (Dashboard)

- **Kennzahlenleiste** mit der nächsten Stunde als erste Zelle sowie Zahlen zu offenen Aufgaben, Prüfungen usw.
- **Stundenplan** der Woche, beginnend mit „Heute“; Zeilen sind die Uhrzeit in Ortszeit, ausgefallene Stunden sind markiert.
- **Hausaufgaben, Prüfungen, Termine** in der Hauptspalte.
- **Willkommenssatz:** einmal pro Tag erzeugt (lokal, online oder als feste Formulierung). Ein Badge zeigt, woher er stammt – `lokal`, `Provider` oder `Fallback` –, damit niemand einen festen Satz für eine KI-Aussage hält. „Neu erzeugen“ überschreibt den Satz des Tages.
- **Assistenten-Karte** mit dem Faultier-Maskottchen als Einstieg in den Chat.

---

## Stundenplan

Drei Wege, einen Stundenplan zu bekommen:

1. **WebUntis-Import** – Schule, Kürzel und Zugangsdaten eintragen, dann „Stundenplan aktualisieren“. Geholt werden vier Wochen ab dem aktuellen Montag. Fächer bekommen den ausgeschriebenen Namen und das Kürzel, Lehrkräfte und Räume werden mit angelegt, Ausfälle erkannt. Auf dem Server läuft der Abruf zusätzlich planmäßig (Standard: einmal täglich).
2. **Aus PDF oder Foto** – die KI liest den Plan aus und macht einen **Vorschlag**; gespeichert wird erst nach Durchsicht und Korrektur.
3. **Von Hand** – Stunden direkt anlegen und bearbeiten.

Manuell angelegte Stunden bleiben bei einem neuen Import unangetastet; Fächer mit gleichem Namen werden wiederverwendet statt doppelt angelegt. Fächer können eine eigene Farbe haben.

---

## Hausaufgaben, Prüfungen und Termine

- **Hausaufgaben** mit Fach, Fälligkeit und Status.
- **Prüfungen** mit Datum, Fach und Themen. Aus einer Prüfung lässt sich per **Klausur-Generator** ein Lernplan erzeugen (siehe unten).
- **Termine** (Kalenderereignisse), auch ganztägig. Sie können mit externen Kalendern abgeglichen werden (siehe [Integrationen](#integrationen)).

Alles davon kann auch der Assistent vorschlagen („Trag mir die Mathe-Arbeit nächsten Donnerstag ein“) – eingetragen wird erst nach Klick auf „Eintragen“.

---

## Lernverwaltung: Pläne, Serien, Aufgaben

Auf `/lernen` gibt es drei Karten:

- **Lernpläne** – Container für zusammengehörige Aufgaben (z. B. „Abi Mathe“).
- **Serien** – wiederkehrende Aufgaben (täglich, wöchentlich, monatlich …). Es existiert immer nur die *nächste* Aufgabe; erst beim Erledigen entsteht die folgende. Monatlich rechnet in echten Monaten (31. Januar + 1 Monat = 28./29. Februar).
- **Aufgaben** – mit Priorität 0–5 (höher = wichtiger), Fälligkeit, Thema und Lernzeit-Messung (Start/Stopp).

**Abhängigkeiten:** Eine Aufgabe kann auf andere Aufgaben im selben Plan warten. Zyklen werden beim Anlegen abgelehnt. Eine blockierte Aufgabe wird nicht versteckt, sondern mit „Wartet auf …“ angezeigt; ein Start schlägt mit Hinweis fehl. Stornierte Aufgaben gelten für Abhängigkeiten als erledigt.

**Klausur-Generator:** Aus einer Prüfung mit Themen entsteht eine Kette von Lernaufgaben. Das **unsicherste Thema** (niedrigster Lernstand, ohne Wert = unsicher) bekommt den frühesten Termin und die höchste Priorität, weil es am meisten Vorlauf braucht. Ein zweiter Klick erzeugt nichts doppelt.

---

## Wochenplaner

Oben auf `/lernen`: Aus **Stundenplan + offenen Aufgaben + Hausaufgaben + Prüfungen + fälligen Wiederholungen** entsteht ein konkreter Lernplan für die Woche.

- **Vorschau zuerst**, dann „Übernehmen“. Die Übernahme rechnet mit dem aktuellen Stand neu.
- **Deterministisch, ohne KI** – Fristen, Tagesbudget und Abhängigkeiten werden exakt eingehalten.
- Belegt sind Unterrichtsstunden (plus Pause danach), Termine (außer ganztägigen) und bereits terminierte Aufgaben.
- Reihenfolge: Abhängigkeiten → früheste Frist → höhere Priorität. Möglichst ein Block je Klausur und Tag; was vor der Frist keinen Platz findet, wird als „spät“ oder mit Begründung als „nicht platziert“ gemeldet.
- Wird ein Hausaufgaben-Block erledigt, ist die Hausaufgabe mit abgehakt.
- Einstellungen (Tagesbudget, Vorlauf vor Klausuren usw.) merkt sich der Browser.

---

## Lernfortschritt und Wiederholungen

Auf `/fortschritt`:

- **Lernstand je Thema** in fünf Stufen mit festem Wiederholungsintervall:

  | Stufe | Nächste Wiederholung nach |
  | --- | --- |
  | unsicher | 1 Tag |
  | geübt | 3 Tagen |
  | sicher | 7 Tagen |
  | fest | 14 Tagen |
  | gefestigt | 30 Tagen |

  Kein komplexes SRS – die Regel lässt sich in einem Satz erklären. Ein nie wiederholtes Thema hat keinen Termin, gilt aber als unsicher.
- **Lernprotokoll** aus den gemessenen Lernsessions: Minuten je Fach, Verteilung über die Wochentage (letzte 12 Wochen), offene Sessions separat.
- **Serie (Streak):** Tage in Folge mit mindestens einer abgeschlossenen Lernminute; heute zählt erst als verpasst, wenn auch der Tag vorbei ist.
- **Wochenziel** in Minuten (Standard 300), Woche Montag–Sonntag in der eigenen Zeitzone.

Der Lernstand fließt in Klausur-Generator, Wochenplaner und das Lernprofil des Assistenten ein.

---

## Noten

- **Erfassen** mit Note, Art (Klassenarbeit, Test, mündlich …), Gewichtung, Datum, Fach und Kommentar.
- **Schnitt** je Fach und gesamt, immer gewichtet; **Verlauf** als schlichte Linie.
- **Hochrechnung:** Was muss ab jetzt kommen, damit der Schnitt die Bestehensgrenze erreicht? Ist das außerhalb von 1,0–6,0 nicht mehr erreichbar, wird das ehrlich angezeigt statt geklemmt.
- **Import aus einem Dokument:** eine Notenliste wird zeilenweise gelesen, als Vorschau gezeigt und erst nach Bestätigung übernommen.
- **Noten im Lernplan:** Schlechte Noten in einem Fach heben die Priorität der zugehörigen Klausuraufgaben.
- **Freigabe für die KI:** Noten gehen nur mit ausdrücklicher Zustimmung an einen Online-KI-Dienst (Standard: aus). Läuft die KI komplett lokal, darf der Assistent die Noten auch ohne Freigabe sehen.

---

## Belohnungen

- **Punkte** gibt es für echtes Lernen, nicht fürs Abhaken: Die Einheit ist der **Fachtag** – ein Fach an einem Tag mit genügend abgeschlossener Lernzeit an einem Thema. Gestaffelt und gedeckelt.
- **Auszeichnungen** mit Bedingung, Fortschrittsanzeige und Zeitpunkt.
- Punkte werden als Ereignisliste geführt, ein doppelter Klick schreibt nichts doppelt gut.
- **Punkte beeinflussen niemals** Noten, Prioritäten oder die KI – das ist per Test abgesichert.

---

## Dokumente und Unterrichtsmaterial

Auf `/dokumente`:

- **Hochladen** von Text, PDFs und Fotos. Die Textebene von PDFs wird sofort gelesen; Fotos und Scanseiten werden im Hintergrund von einem lokalen Bildmodell (Qwen2.5-VL) abgeschrieben – oder, falls keins da ist, vom Online-KI-Dienst. Ohne beides bleibt das Dokument als „braucht Modell“ markiert, statt zu raten.
- **Automatische Einordnung** nach festen Regeln: Art (Aufschrieb, Arbeitsblatt, Lösung, Sonstiges), Fach, Thema (erste Überschrift) und Datum – aus Dateiname und Textanfang. Eigene Korrekturen werden nie überschrieben.
- **Bedeutungssuche:** Texte werden in Abschnitte geteilt und lokal mit bge-m3 eingebettet, sodass der Assistent passende Stellen auch ohne exakte Stichworte findet.
- Dokumente lassen sich auch aus einem lokalen Ordner abrufen (Integration „Dokumente“).

---

## Lern-Assistent

Auf `/assistent` – ein Chat, der **auf alle eigenen Daten zugreift**, aber nichts erfindet:

- **Kontext im Prompt:** die nächsten 14 Tage als Tabelle, Stundenplan, Aufgaben, Prüfungen, Liste aller Dokumente, passende Auszüge aus dem Material (mit Art, Fach, Thema, Datum), die Einzelnoten (mit Freigabe bzw. lokal) und ein **Lernprofil** (unsichere/fällige/sichere Themen, Lernzeiten, Abfrage-Ergebnisse). Findet sich nichts, steht ausdrücklich „nichts gefunden“ im Prompt.
- **Dokumente beim Namen:** „Was steht in meinem Lebenslauf?“ lädt das Dokument über den Dateinamen.
- **Eintragen per Vorschlag:** Wünscht man einen Eintrag, schlägt der Assistent Prüfung, Hausaufgabe oder Termin vor; der Knopf „Eintragen“ legt ihn an. Datumsangaben wie „übermorgen“, „nächsten Freitag“ oder „12.11.“ rechnet die App selbst nach, statt dem Modell zu vertrauen.
- **Gedächtnis:** Mit `MERKE:` speichert der Assistent Fakten über dich (sichtbar und löschbar). Ähnliche Notizen werden nicht doppelt gespeichert.
- **Abfrage (Quiz):** Der Assistent fragt Themen ab; das Ergebnis verändert den Lernstand eines vorhandenen Themas (+/−). Ohne Modell gibt es Lückenfragen mit Bewertung über Begriffe.
- **Wahl der KI:** Man kann ein bestimmtes lokales Modell oder eine bestimmte Verbindung auswählen; dann wird nur diese benutzt.
- **Bewertung** per Daumen hoch/runter (wird fürs Training genutzt, wenn freigegeben).
- Der **Verlauf** bleibt im Browser, der Server speichert keine Gespräche.

**Faultier-Maskottchen:** Eine schwarz-weiße Figur zeigt den Zustand – ruhig, zuhörend, nachdenkend, sprechend, sich freuend (nach einem Eintrag) oder schlafend (keine KI verfügbar). Die Augen folgen dem Mauszeiger.

---

## KI: lokal, online oder ohne

Jeder KI-Aufruf geht denselben Weg:

```text
eingebautes lokales Modell → eigene lokale Verbindung (Ollama, LM Studio …) → Online-Anbieter → feste Ersatzantwort
```

Jede Antwort trägt ihre Herkunft (`local`, `provider`, `fallback`), und die Oberfläche zeigt sie an. Eine kaputte KI-Konfiguration legt nie die App lahm.

**Auf `/ki`:**

- **KI-Dienst verbinden:** jede OpenAI-kompatible Schnittstelle (OpenAI, Ollama, LM Studio, llama-server …). Der Verbindungstest fragt nur die Modellliste ab – kostet keine Tokens. „Auto“ wählt das erste verfügbare Modell.
- **Lokale Modelle (Desktop):** ein Katalog mit Text-, Bild-, Einbettungs-, Spracherkennungs- und Stimmmodellen zum Herunterladen. Die nötigen Programme (llama.cpp, whisper.cpp, Piper) lädt die App selbst – mit fester Version und Prüfsumme, nur von GitHub. Modelle starten erst bei Bedarf, höchstens ein Textmodell gleichzeitig, und beim Beenden der App wird alles mit beendet.

---

## Sprache: Sprechen und Vorlesen

- **Spracheingabe:** Mikrofon-Knopf halten, das Transkript wird direkt abgeschickt (whisper.cpp, lokal).
- **Vorlesen:** Antworten werden mit Piper gesprochen; Stimmen passend zur Oberflächensprache.
- **Gesprächsmodus („Gespräch starten“):** Mikrofon bleibt offen, Satzende wird über Stille erkannt, die Antwort ist kurz und vorlesbar, danach hört der Assistent wieder zu. Ein gesprochenes „Ja“/„Nein“ bestätigt oder verwirft offene Vorschläge – ohne Modellaufruf.

---

## Persönliches Training (LoRA)

Optional und nur mit ausdrücklicher Freigabe: Der Assistent passt sich dem eigenen Stil an.

- **Trainingsbeispiele:** Mit der Freigabe „Aus Gesprächen lernen“ wird jede echte Modellantwort samt Signalen gespeichert – eingetragene oder verworfene Vorschläge, gelöschte Notizen, Daumen. Daraus wird gerechnet, ob ein Beispiel gut, korrigiert oder schlecht war.
- **Material:** Mit eigener Freigabe lernt das Modell auch den Aufbau der eigenen Aufschriebe und das Beantworten von Fragen aus dem Material.
- **Training** läuft als eigener Prozess (QLoRA, 4 Bit) auf NVIDIA-GPUs ab 6 GB; Apple Silicon (MLX) folgt. Es lässt sich pausieren und fortsetzen; optional startet es automatisch, wenn der Rechner am Netz und unbenutzt ist.
- **Aktiviert** wird ein neuer Adapter nur, wenn er auf Prüfbeispielen nicht schlechter ist als ohne. Versionen lassen sich zurückrollen.
- **Fakten** werden nie nur ins Modell trainiert, sie bleiben im Prompt. Trainiert wird das Verhalten (Datumsangaben, Ton, Aufbau).

---

## Integrationen

In den Einstellungen (KI-Verbindungen auf `/ki`). Alle Zugangsdaten werden **verschlüsselt** gespeichert (AES-GCM) und nur im Backend entschlüsselt. Jede Integration hat einen echten Verbindungstest.

| Integration | Funktion |
| --- | --- |
| **WebUntis** | Stundenplan, Fächer, Lehrkräfte, Räume, Ausfälle importieren |
| **KI-Anbieter** | OpenAI-kompatible Schnittstelle für Assistent, Begrüßung, Texterkennung |
| **Dokumente** | Unterlagen aus einem lokalen Ordner abrufen |
| **iCalendar** | Kalender aus einer ICS-Adresse importieren |
| **Google Kalender** | lesen und schreiben (OAuth 2.0) |
| **Apple Kalender (CalDAV)** | lesen und schreiben (App-spezifisches Passwort) |

**Kalender-Abo:** Die eigenen Termine gibt es als abonnierbare ICS-Adresse mit widerrufbarem Token. Schreiben in externe Kalender erfordert eine eigene Freigabe (Standard: aus).

---

## Benachrichtigungen

- **Erinnerungen** an fällige Lernaufgaben und Prüfungen, von einem Hintergrund-Thread alle paar Minuten geprüft.
- **Kanäle:** Web-Push und lokale Benachrichtigungen bei offener Seite.
- **Einstellungen je Nutzer:** Hauptschalter, Kanäle, Uhrzeit der Tageserinnerung, **Ruhezeiten** (Standard 22:00–07:00, in der eigenen Zeitzone). In der Ruhezeit wird nicht zugestellt, sondern aufs Ruheende verschoben.
- Im Zweifel gilt: kaputte Einstellung = keine Benachrichtigung.

---

## Datenschutz und Konto

- **Alles gehört dem Nutzer:** Jede Anfrage ist auf den angemeldeten Nutzer beschränkt; fremde Daten liefern „nicht gefunden“.
- **Freigaben sind standardmäßig aus:** Noten an die KI, Kalender schreiben, Training aus Gesprächen, Training aus Material, automatisches Training.
- **Datenauskunft** (DSGVO Art. 15/20): Export aller eigenen Daten als Datei, ohne Passwort-Hash, verschlüsselte Zugangsdaten und Tokens.
- **Konto löschen** (Art. 17): entfernt alle Daten aus allen Tabellen. Zur Bestätigung tippt man „LÖSCHEN“ (englisch „DELETE“).
- **Lokal-zuerst:** Mit Desktop-App und lokalen Modellen verlässt nichts den Rechner.
- Eine Datenschutzseite gibt es auf Deutsch und Englisch.

---

## Mehrsprachigkeit

- Oberfläche auf **Deutsch und Englisch**, umschaltbar in den Einstellungen (sonst nach Browsersprache).
- Daten, Zahlen und Wochentage werden im passenden Format angezeigt.
- Der Assistent antwortet in der Oberflächensprache, die Begrüßung ebenso, Spracherkennung und Stimme folgen ihr.
- Die Desktop-Hülle (Menü, Dialoge, Startbildschirm) ist ebenfalls zweisprachig.

---

## Backups

- **Server:** Der Worker erstellt planmäßig `pg_dump`-Sicherungen der Datenbank; ihr Stand ist in der App sichtbar. Hochgeladene Dokumente liegen als Dateien und müssen mitgesichert werden – siehe [server.md](server.md).
- **Desktop:** Der Datenordner (`%APPDATA%\Slearn` bzw. `~/Library/Application Support/Slearn`) enthält alles; vor jedem Update sichert die App die Datenbank selbst.

---

## Technischer Aufbau

```text
Browser ──/api/backend/v1/*──▶ Next.js (Proxy)
                                   └─▶ FastAPI-Backend /api/v1/*
                                           └─▶ PostgreSQL + pgvector  (Desktop: SQLite)
Worker ──▶ Datenbank: Healthcheck, Heartbeat, pg_dump-Backups
Trainer ──▶ eigener Python-Prozess für LoRA-Training (GPU)
```

| Teil | Technik |
| --- | --- |
| Frontend | Next.js 16, React 19, Tailwind v4, Zod; schwarz-weißes Design, Schrift Geist |
| Backend | FastAPI, SQLAlchemy, Alembic (16 Migrationen) |
| Datenbank | PostgreSQL 15 + pgvector bzw. SQLite |
| Hintergrund | Threads im Backend: WebUntis-Abruf, Erinnerungen, Material-Indexer |
| Worker | Python, nur `psycopg` |
| Desktop | Electron + PyInstaller-Backend + Next-Standalone-Server |
| Lokale KI | llama.cpp, whisper.cpp, Piper (von der App selbst geladen) |
| Landingpage | eigene Next.js-App mit Warteliste (`landing/`) |

Weiterführend: [api.md](api.md) (alle Endpunkte), [konfiguration.md](konfiguration.md) (Umgebungsvariablen), [desktop.md](desktop.md), [server.md](server.md), [entwicklung.md](entwicklung.md), [AGENTS.md](../AGENTS.md) (Fallstricke je Bereich).
