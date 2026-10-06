# Landingpage mit Warteliste

Die Landingpage liegt in `landing/`. Sie ist eine eigene, kleine Next.js-App mit eigenem `package.json` und Lockfile und teilt zur Build-Zeit nichts mit `frontend/`. So lässt sie sich getrennt von der App veröffentlichen, etwa auf einem Static-/Serverless-Hoster oder hinter demselben Reverse-Proxy wie der Server.

## Aufbau

| Pfad | Inhalt |
| --- | --- |
| `app/page.tsx` | Die ganze Seite: Hero, Funktionen, Assistent, Datenschutz, Desktop/Server, Warteliste, Fragen |
| `components/app-preview.tsx`, `assistant-preview.tsx` | Nachbau von Dashboard und Assistent in HTML statt Screenshots (scharf, Dunkelmodus, keine Bilddatei) |
| `components/faultier.tsx` | Kopie des Maskottchens aus `frontend/components/assistant/faultier.tsx` |
| `components/waitlist-form.tsx` | Formular (kompakt im Hero, vollständig im Abschnitt „Warteliste“) |
| `lib/waitlist.ts` | Prüfung der Eingabe und Mengenbegrenzung, ohne Speicher – getestet in `lib/waitlist.test.ts` |
| `lib/waitlist-store.ts` | Speichern in Supabase oder einer JSONL-Datei |
| `app/api/waitlist/route.ts` | `POST /api/waitlist` |
| `supabase/waitlist.sql` | Tabelle für Supabase |

Design: dieselben Graustufen-Tokens und Regeln wie die App (siehe „Frontend-UI-Konventionen“ in `AGENTS.md`). Das Farbschema folgt dem System (`prefers-color-scheme`), einen Umschalter gibt es nicht. Die CSP ist so streng wie in der App: keine fremden Skripte, Schriften oder Bilder.

## Befehle

```bash
cd landing
npm ci
npm run dev        # http://localhost:3100
npm test && npm run lint && npm run typecheck
npm run build      # next build --webpack, wie in der App
npm start
```

## Warteliste

`POST /api/waitlist` mit JSON `{ email, name?, role?, platform?, consent, website? }`.

- `role`: `schueler`, `eltern`, `lehrkraft`, `andere`. `platform`: `windows`, `macos`, `server`.
- `consent` muss `true` sein (Einwilligung zur E-Mail beim Start).
- `website` ist ein Honigtopf-Feld. Ist es gefüllt, meldet die Route Erfolg und speichert nichts.
- **Neue und schon bekannte Adressen bekommen dieselbe Antwort**, damit sich über das Formular nicht prüfen lässt, wer eingetragen ist.
- Fehler: 422 mit `field` und deutscher Meldung, 403 bei fremdem `Origin`, 429 nach 5 Versuchen in 10 Minuten je IP (im Prozessspeicher – auf mehreren Instanzen zählt jede für sich), 503, wenn der Speicher nicht erreichbar ist.
- Ein `?ref=…` in der Seitenadresse wird als `source` gespeichert (z. B. `?ref=instagram`).

### Speicher

| Variable | Bedeutung |
| --- | --- |
| `WAITLIST_SUPABASE_URL` | Projekt-URL, z. B. `https://abc.supabase.co`. Zusammen mit dem Schlüssel aktiviert sie Supabase. |
| `WAITLIST_SUPABASE_SERVICE_ROLE_KEY` | Service-Role-Schlüssel. Nur serverseitig, **nie** mit `NEXT_PUBLIC_`-Präfix. |
| `WAITLIST_FILE` | Ohne Supabase: Pfad der JSONL-Datei, Standard `landing/.data/waitlist.jsonl` (gitignoriert). |
| `LANDING_SITE_URL` | Öffentliche Adresse für die Open-Graph-Metadaten, Standard `http://localhost:3100`. |

Für Supabase einmal `supabase/waitlist.sql` im SQL-Editor ausführen. Die Tabelle hat RLS ohne Policy, der öffentliche `anon`-Schlüssel kommt also nicht heran; geschrieben wird nur vom Server mit dem Service-Role-Schlüssel. Auf Serverless-Hostern ist das Dateisystem flüchtig oder schreibgeschützt – dort ist Supabase Pflicht, sonst antwortet die Route mit 503.

Export der Liste aus der Datei: `jq -r '.email' landing/.data/waitlist.jsonl`.
