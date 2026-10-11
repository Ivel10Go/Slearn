/*
 * Warteliste als Supabase Edge Function – fuer die statische Seite auf GitHub
 * Pages, die keine eigene Route `/api/waitlist` hat. Gleiche Regeln wie
 * `app/api/waitlist/route.ts`:
 *
 * - Neue und schon bekannte Adressen bekommen dieselbe Antwort.
 * - Das Honigtopf-Feld `website` meldet Erfolg und speichert nichts.
 * - Mengenbegrenzung je IP im Speicher der Instanz (jede Instanz zaehlt fuer sich).
 *
 * `waitlist.ts` ist eine **Kopie** von `lib/waitlist.ts`, weil die Function nur
 * Dateien aus ihrem eigenen Ordner mitnimmt. `lib/function-copy.test.ts` faellt,
 * sobald die beiden auseinanderlaufen.
 *
 * Umgebung (Secrets der Function):
 * - `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` setzt Supabase selbst.
 * - `WAITLIST_ALLOWED_ORIGINS`: kommagetrennte Herkuenfte, die schreiben duerfen,
 *   z. B. `https://ivel10go.github.io`. Leer = jede Herkunft.
 *
 * Deploy ohne JWT-Pruefung (`--no-verify-jwt`): das Formular ist oeffentlich und
 * schickt keinen Schluessel mit.
 */
import { parseWaitlistInput, RateLimiter } from "./waitlist.ts";

const limiter = new RateLimiter();
const MAX_BODY_BYTES = 4096;
const SUCCESS = { ok: true, message: "Danke! Du stehst auf der Warteliste. Wir melden uns zum Start." };

const allowedOrigins = (Deno.env.get("WAITLIST_ALLOWED_ORIGINS") ?? "")
  .split(",")
  .map((origin) => origin.trim().replace(/\/+$/, ""))
  .filter(Boolean);

function originAllowed(origin: string | null): boolean {
  // Ohne Origin-Header (Werkzeuge) entscheidet die Mengenbegrenzung allein.
  return !origin || allowedOrigins.length === 0 || allowedOrigins.includes(origin);
}

function corsHeaders(origin: string | null): Record<string, string> {
  const headers: Record<string, string> = {
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "content-type",
    "Access-Control-Max-Age": "86400",
    Vary: "Origin",
  };
  if (origin && originAllowed(origin)) {
    headers["Access-Control-Allow-Origin"] = origin;
  }
  return headers;
}

function json(origin: string | null, status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...corsHeaders(origin), "Content-Type": "application/json; charset=utf-8" },
  });
}

function error(origin: string | null, status: number, message: string, field = "form"): Response {
  return json(origin, status, { ok: false, field, message });
}

function clientKey(request: Request): string {
  const forwarded = request.headers.get("x-forwarded-for")?.split(",")[0]?.trim();
  return forwarded || request.headers.get("x-real-ip") || "unbekannt";
}

async function save(entry: unknown, source: string | null): Promise<void> {
  const baseUrl = Deno.env.get("SUPABASE_URL")?.replace(/\/+$/, "");
  const key = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  if (!baseUrl || !key) {
    throw new Error("SUPABASE_URL oder SUPABASE_SERVICE_ROLE_KEY fehlt");
  }
  const response = await fetch(`${baseUrl}/rest/v1/waitlist?on_conflict=email`, {
    method: "POST",
    headers: {
      apikey: key,
      Authorization: `Bearer ${key}`,
      "Content-Type": "application/json",
      // Bei einer vorhandenen Adresse nichts aendern.
      Prefer: "resolution=ignore-duplicates,return=minimal",
    },
    body: JSON.stringify({ ...(entry as object), source }),
    signal: AbortSignal.timeout(8000),
  });
  if (!response.ok) {
    throw new Error(`Supabase antwortete mit ${response.status}: ${await response.text()}`);
  }
}

Deno.serve(async (request) => {
  const origin = request.headers.get("origin");
  if (request.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: corsHeaders(origin) });
  }
  if (request.method !== "POST") {
    return error(origin, 405, "Nur POST ist erlaubt.");
  }
  if (!originAllowed(origin)) {
    return error(origin, 403, "Diese Anfrage kommt nicht von der Lernraum-Seite.");
  }
  if (!limiter.allow(clientKey(request))) {
    return error(origin, 429, "Zu viele Versuche. Bitte versuche es in ein paar Minuten noch einmal.");
  }

  const raw = await request.text();
  if (raw.length > MAX_BODY_BYTES) {
    return error(origin, 413, "Die Anfrage ist zu groß.");
  }
  let body: unknown;
  try {
    body = JSON.parse(raw);
  } catch {
    return error(origin, 400, "Die Anfrage war leer oder unlesbar.");
  }

  const parsed = parseWaitlistInput(body);
  if (!parsed.ok) {
    return error(origin, 422, parsed.message, parsed.field);
  }
  if (parsed.spam) {
    return json(origin, 200, SUCCESS);
  }

  // Der Referer kommt quer ueber Herkuenfte ohne Query an, deshalb schickt das
  // Formular `?ref=` selbst mit.
  const rawSource = (body as { source?: unknown }).source;
  const source = typeof rawSource === "string" && rawSource.trim() ? rawSource.trim().slice(0, 64) : null;
  try {
    await save(parsed.entry, source);
  } catch (cause) {
    console.error((cause as Error).message);
    return error(origin, 503, "Die Warteliste ist gerade nicht erreichbar. Bitte versuche es später noch einmal.");
  }
  return json(origin, 200, SUCCESS);
});
