# Landingpage mit Warteliste

Die Landingpage liegt in `landing/`. Sie ist eine eigene, kleine Next.js-App mit eigenem `package.json` und Lockfile und teilt zur Build-Zeit nichts mit `frontend/`. So lässt sie sich getrennt von der App veröffentlichen, etwa auf einem Static-/Serverless-Hoster oder hinter demselben Reverse-Proxy wie der Server.

## Aufbau

| Pfad | Inhalt |
| --- | --- |
| `app/(de)/`, `app/en/` | Je ein Wurzel-Layout und eine Seite pro Sprache (`/` deutsch, `/en/` englisch) |
| `components/landing-page.tsx` | Die ganze Seite: Hero, Funktionen, Assistent, Datenschutz, Desktop/Server, Warteliste, Fragen |
| `lib/content.ts` | Alle Texte, je Sprache ein ganzes Objekt (`LandingContent`) |
| `components/language-hint.tsx`, `lib/locale.ts` | Sprachwahl merken, Hinweis auf die andere Sprache |
| `components/app-preview.tsx`, `assistant-preview.tsx` | Nachbau von Dashboard und Assistent in HTML statt Screenshots (scharf, Dunkelmodus, keine Bilddatei) |
| `components/faultier.tsx` | Kopie des Maskottchens aus `frontend/components/assistant/faultier.tsx` |
| `components/waitlist-form.tsx` | Formular (kompakt im Hero, vollständig im Abschnitt „Warteliste“) |
| `lib/waitlist.ts` | Prüfung der Eingabe und Mengenbegrenzung, ohne Speicher – getestet in `lib/waitlist.test.ts` |
| `lib/waitlist-store.ts` | Speichern in Supabase oder einer JSONL-Datei |
| `app/api/waitlist/route.ts` | `POST /api/waitlist` |
| `supabase/waitlist.sql` | Tabelle für Supabase |

Design: dieselben Graustufen-Tokens und Regeln wie die App (siehe „Frontend-UI-Konventionen“ in `AGENTS.md`). Das Farbschema folgt dem System (`prefers-color-scheme`), einen Umschalter gibt es nicht. Die CSP ist so streng wie in der App: keine fremden Skripte, Schriften oder Bilder.

## Sprachen

Deutsch unter `/`, Englisch unter `/en/`. Alle Texte stehen in `lib/content.ts`, je Sprache ein ganzes Objekt – kein Schlüssel-Wörterbuch wie in der App, weil die Seite fast nur aus Fließtext besteht. `lib/content.test.ts` prüft, dass beide Sprachen dieselbe Gestalt haben (gleich lange Listen, kein leerer Text) und dass die deutschen Formularmeldungen wörtlich denen von `parseWaitlistInput()` entsprechen.

- **Zwei Wurzel-Layouts** (`app/(de)/layout.tsx`, `app/en/layout.tsx`, gemeinsam `components/root-document.tsx`), damit `<html lang>`, Titel, `hreflang`-Alternativen und `og:locale` je Sprache stimmen – ohne Server lässt sich `lang` nicht je Anfrage setzen.
- **Sprachlinks sind schlichte `<a>` mit `homeHref()`**, nicht `next/link`: zwischen zwei Wurzel-Layouts lädt Next ohnehin neu, und das Vorladen fragte im Export eine nicht vorhandene Datei der Routengruppe an.
- **Keine automatische Umleitung nach Browsersprache.** Gemerkt wird nur eine *Wahl* (`localStorage` `lernraum.sprache`, gesetzt beim Klick auf einen Link mit `data-sprache`); wer sich die andere Sprache gemerkt hat, wird weitergeleitet. Ohne Wahl zeigt `LanguageHint` nur einen Hinweis, wenn der Browser die andere Sprache bevorzugt – sonst sähen Suchmaschinen mit englischem Browser die deutsche Seite nie.
- **Formularmeldungen kommen aus den Texten der Seite**, nicht aus der Antwort: Route und Edge Function antworten weiter deutsch, `lib/waitlist-messages.ts` wählt die Meldung nur nach `field` und Status. Dadurch musste die Edge Function nicht geändert werden.
- Die Sprungmarken sind je Sprache verschieden (`#funktionen` / `#features`, siehe `ids`), damit bestehende deutsche Links gültig bleiben.
- Eine weitere Sprache: Objekt in `lib/content.ts` ergänzen, `Locale`/`LOCALES` erweitern, `app/<sprache>/layout.tsx` + `page.tsx` anlegen; `otherLocale()` und der Umschalter gehen heute von genau zwei Sprachen aus.

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

## GitHub Pages

Der Workflow `.github/workflows/landing-pages.yml` baut die Seite bei jedem Push auf `main`, der `landing/` berührt (oder von Hand), als statischen Export und veröffentlicht sie unter `https://ivel10go.github.io/School-assistant/`.

- **Statischer Export:** `STATIC_EXPORT=true` setzt `output: "export"`, `BASE_PATH` den Repo-Pfad (liefert `actions/configure-pages`). Route-Handler kann ein Export nicht bauen, deshalb löscht der Workflow `app/api` vor dem Bau. Antwort-Header gibt es auf Pages nicht: die CSP steht dann als `<meta>` im Layout (ohne `frame-ancestors`, das in `<meta>` nicht gilt).
- **Warteliste über die Edge Function** `supabase/functions/waitlist` (gleiche Regeln, gleiche Antworten wie die Route). `waitlist.ts` darin ist eine Kopie von `lib/waitlist.ts`; `lib/function-copy.test.ts` fällt, wenn sie abweicht – nach Änderungen an den Regeln `cp lib/waitlist.ts supabase/functions/waitlist/waitlist.ts`.
- Weil der Referer quer über Herkünfte ohne Query ankommt, schickt das Formular `?ref=` selbst als `source` mit.

Einmalig einrichten:

1. Im Supabase-Projekt `supabase/waitlist.sql` ausführen.
2. Function deployen: `supabase functions deploy waitlist --no-verify-jwt` (das Formular schickt keinen Schlüssel) und das Secret `WAITLIST_ALLOWED_ORIGINS=https://ivel10go.github.io` setzen. `SUPABASE_URL` und `SUPABASE_SERVICE_ROLE_KEY` stellt Supabase selbst bereit.
3. Im GitHub-Repo die Actions-Variable `WAITLIST_URL` = `https://<projekt>.supabase.co/functions/v1/waitlist` anlegen. Ohne sie zeigt das Formular auf Pages nur einen Fehler.
4. Settings → Pages → Source: **GitHub Actions**.

Lokal prüfen (bash; `MSYS_NO_PATHCONV=1` nur unter Git Bash, sonst wird `/School-assistant` zu einem Windows-Pfad):

```bash
mv app/api /tmp/landing-api
MSYS_NO_PATHCONV=1 STATIC_EXPORT=true BASE_PATH=/School-assistant npx next build --webpack
mv /tmp/landing-api app/api
```
