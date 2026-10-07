import { NextResponse, type NextRequest } from "next/server";
import { parseWaitlistInput, RateLimiter } from "@/lib/waitlist";
import { saveWaitlistEntry, WaitlistStoreError } from "@/lib/waitlist-store";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const limiter = new RateLimiter();
const MAX_BODY_BYTES = 4096;
const SUCCESS = { ok: true, message: "Danke! Du stehst auf der Warteliste. Wir melden uns zum Start." };

function clientKey(request: NextRequest): string {
  const forwarded = request.headers.get("x-forwarded-for")?.split(",")[0]?.trim();
  return forwarded || request.headers.get("x-real-ip") || "unbekannt";
}

// Nur das eigene Formular darf schreiben. Fehlt der Origin-Header (manche
// Werkzeuge senden keinen), entscheidet die Mengenbegrenzung allein.
function sameOrigin(request: NextRequest): boolean {
  const origin = request.headers.get("origin");
  if (!origin) {
    return true;
  }
  try {
    return new URL(origin).host === request.headers.get("host");
  } catch {
    return false;
  }
}

function error(status: number, message: string, field = "form") {
  return NextResponse.json({ ok: false, field, message }, { status });
}

export async function POST(request: NextRequest) {
  if (!sameOrigin(request)) {
    return error(403, "Diese Anfrage kommt nicht von der Lernraum-Seite.");
  }
  if (!limiter.allow(clientKey(request))) {
    return error(429, "Zu viele Versuche. Bitte versuche es in ein paar Minuten noch einmal.");
  }

  const raw = await request.text();
  if (raw.length > MAX_BODY_BYTES) {
    return error(413, "Die Anfrage ist zu groß.");
  }
  let body: unknown;
  try {
    body = JSON.parse(raw);
  } catch {
    return error(400, "Die Anfrage war leer oder unlesbar.");
  }

  const parsed = parseWaitlistInput(body);
  if (!parsed.ok) {
    return error(422, parsed.message, parsed.field);
  }
  if (parsed.spam) {
    return NextResponse.json(SUCCESS);
  }

  const referer = request.headers.get("referer");
  const source = referer ? new URL(referer).searchParams.get("ref")?.slice(0, 64) ?? null : null;
  try {
    await saveWaitlistEntry(parsed.entry, source);
  } catch (cause) {
    if (cause instanceof WaitlistStoreError) {
      console.error(cause.message);
      return error(503, "Die Warteliste ist gerade nicht erreichbar. Bitte versuche es später noch einmal.");
    }
    throw cause;
  }
  return NextResponse.json(SUCCESS);
}
