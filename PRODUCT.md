# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Schüler, die ihren Schulalltag selbst organisieren (Stundenplan, Hausaufgaben, Klausuren, Lernen), am Rechner in der Desktop-App oder im Browser. Zusätzlich Eltern, die mitsehen oder mitverwalten (Termine, Noten). Stand: privat genutzt, eine spätere öffentliche Verbreitung ist offen und nicht entschieden.

## Product Purpose

Slearn bündelt Stundenplan (WebUntis-Import), Hausaufgaben, Klausuren, Lernpläne, Noten, Fortschritt und eigenes Unterrichtsmaterial an einem Ort und beantwortet zwei Fragen: „Was steht heute an?“ (Übersicht) und „Wie stehe ich?“ (Fortschritt). Erfolg heißt: die Lernenden planen und lernen tatsächlich, statt nur Listen zu pflegen.

## Positioning

Was eine andere Lern-App nicht ehrlich kopieren kann:
- **Lokal und privat:** KI, Spracheingabe und Daten laufen auf dem eigenen Gerät, ohne Cloud-Zwang.
- **Lernt an dir:** merkt sich Fakten, führt ein Lernprofil, trainiert optional einen persönlichen LoRA-Adapter.
- **Alles aus einem Guss:** Stundenplan, Aufgaben, Klausuren, Lernplan und Material in einer App; der Wochenplan ist deterministisch gerechnet, kein Modell-Würfeln.
- **Eigenes Unterrichtsmaterial:** Fotos und Scans von Aufschrieben werden gelesen und für Fragen und Abfragen genutzt.

## Operating Context

Schulalltag: Stundenplan aus dem WebUntis-Portal der Schule (Lessing-Gymnasium Karlsruhe), Klausurphasen, Hausaufgaben mit Fristen. Läuft als Desktop-App (Windows/macOS, kein Login, kein Docker) oder als Docker-Stack mit Next.js, FastAPI und PostgreSQL. Die KI-Antwort kommt lokal, vom Online-Anbieter oder als feste Ersatzantwort; die Oberfläche zeigt die Herkunft.

## Capabilities and Constraints

- Oberfläche zweisprachig (Deutsch/Englisch); neue Texte deutsch durch `t()`, englischer Eintrag in `frontend/lib/i18n/en.ts`.
- Routen: Übersicht `/`, `/lernen`, `/faecher`, `/dokumente`, `/assistent`, `/fortschritt`, `/ki`, `/settings`, `/offline`.
- Strikte CSP: keine externen Schriften, Skripte oder Bilder zur Laufzeit.
- Zustände werden ehrlich benannt (z. B. blockierte Aufgabe mit Blocker, Antwortherkunft als Badge); nichts wird erfunden.
- Offen: Preise, öffentliche Verbreitung, Raspberry-Pi-Betrieb.

## Brand Commitments

Name Slearn (bis 1.1.0 „Lernraum“; interne Kennungen bleiben `lernraum`). Das Faultier-Maskottchen ist Teil der Marke. Das bestehende Design (Schwarz-Weiß, kleine Rundungen, Geist-Schrift, keine Farbklassen, Gradienten oder Schatten) ist bewusst gewählt; Dokumentation steht in `AGENTS.md`.

## Evidence on Hand

Echter Code, Tests und eine Landingpage unter `landing/` mit Warteliste. Keine Nutzerzitate, Kundenzahlen oder Preise vorhanden; nichts davon erfinden.

## Product Principles

1. Ehrlichkeit vor Glanz: Herkunft, Unsicherheit und Lücken zeigen, nichts vortäuschen.
2. Deterministisch, wo es um Termine und Fristen geht; KI nur dort, wo sie Verhalten oder Sprache trägt.
3. Daten bleiben beim Nutzer; Online-Wege sind eine bewusste Freigabe.
4. Ruhig statt motivierend: keine Belohnungsfloskeln, die Oberfläche trägt Inhalt, nicht Stimmung.

## Accessibility & Inclusion

Kein festes Konformitätslevel festgelegt. Zielgruppe ist jung und mobil (Mindestbreite Desktop 960, mobile Bottom-Nav), Bedienung per Tastatur und Sprache (Gesprächsmodus) soll möglich bleiben.
