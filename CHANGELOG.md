# Changelog

Alle nennenswerten Änderungen an Lernraum stehen in dieser Datei.
Das Format folgt [Keep a Changelog](https://keepachangelog.com/de/1.1.0/), die Versionen folgen [Semantic Versioning](https://semver.org/lang/de/).

## [1.1.0] – 2026-10-14

Erste öffentliche Version.

### Hinzugefügt

- **Dashboard „Heute“:** nächste Stunde, Stundenplan der Woche, Hausaufgaben, Prüfungen und Termine auf einen Blick, dazu ein täglicher Begrüßungssatz.
- **Stundenplan:** Abruf aus WebUntis (per Knopf und planmäßig), Ausfälle, parallele Stunden zur Auswahl, Hochladen eines Stundenplans, Fachfarben.
- **Kalender:** iCalendar-Import und abonnierbarer ICS-Export; Google Kalender und Apple Kalender (CalDAV) lesen und schreiben (experimentell).
- **Noten:** Erfassung mit Art und Gewichtung, Schnitt je Fach und gesamt, Verlauf, Hochrechnung und Übernahme aus Dokumenten.
- **Lernpläne:** Pläne, Aufgaben mit Priorität, Frist und Abhängigkeiten, wiederkehrende Serien, Lernaufgaben aus Klausurthemen.
- **Wochenplaner:** verteilt offene Aufgaben, Hausaufgaben und Klausurvorbereitung auf freie Zeit neben dem Stundenplan, mit Vorschau vor dem Übernehmen.
- **Fortschritt:** Lernstand je Thema mit Wiederholungstermin, Lernprotokoll nach Fach und Wochentag, Lernserien und Wochenziel.
- **Belohnungen:** Punkte und Auszeichnungen für Gelerntes, getrennt von Noten und KI.
- **Lern-Assistent:** beantwortet Fragen zu eigenen Unterlagen, Noten und Terminen, schlägt Einträge vor, merkt sich Notizen und fragt Themen ab. Läuft mit eingebauten lokalen Modellen (llama.cpp), eigenem Server (Ollama, LM Studio) oder einem Online-Dienst und zeigt, woher eine Antwort stammt.
- **Lokale Modelle:** Katalog mit fortsetzbaren, prüfsummengesicherten Downloads.
- **Persönliches Training (optional):** LoRA-Anpassung an den eigenen Stil auf NVIDIA-Rechnern, nur mit Freigabe, pausierbar und zurücksetzbar.
- **Sprache:** Spracheingabe mit Whisper, Vorlesen mit Piper-Stimmen, Gesprächsmodus; ohne Modell bleibt der Textmodus voll nutzbar.
- **Unterrichtsmaterial:** Dokumente und Fotos hochladen, Scans abschreiben, Art, Fach und Thema erkennen, Suche nach Bedeutung mit Quellenangaben.
- **Benachrichtigungen:** Erinnerungen an fällige Aufgaben und Klausuren, Web-Push oder lokale Hinweise, mit Uhrzeit und Ruhezeiten.
- **Desktop-App** für Windows und macOS: ohne Docker und ohne Anmeldung, mit automatischen Updates, Infobereich und Autostart.
- **Datenschutz:** Datenschutzseite, Export aller eigenen Daten und Löschen des Kontos.
- **Server-Betrieb:** Docker-Compose-Stack mit PostgreSQL, planmäßigen Backups mit Aufbewahrungsfrist und Backup-Status in den Einstellungen.
- **Zweisprachig:** Oberfläche, Assistent, Sprachausgabe und Landingpage auf Deutsch und Englisch.

### Bekannte Einschränkungen

- Die Installer sind **nicht signiert**: Windows SmartScreen warnt beim ersten Start, unter macOS die App einmal per *Rechtsklick → Öffnen* starten; automatische Updates unter macOS erst mit signierter App.
- Persönliches Training nur auf NVIDIA-Grafikkarten ab 6 GB; Apple Silicon (MLX) folgt.
- Google und Apple Kalender sind bisher nur gegen nachgebildete Antworten getestet.

[1.1.0]: https://github.com/Ivel10Go/School-assistant/releases
