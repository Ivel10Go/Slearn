/*
 * Warteliste: Pruefung der Eingabe und Mengenbegrenzung.
 *
 * Bewusst ohne Speicher und ohne Netz, damit die Regeln ohne Server getestet
 * werden koennen. Gespeichert wird in `waitlist-store.ts`.
 *
 * - **Eine Antwort fuer alle Erfolge.** Ob eine Adresse neu ist oder schon
 *   eingetragen war, sagt die Antwort nicht. Sonst liesse sich ueber das
 *   Formular pruefen, wer auf der Liste steht.
 * - **Das Honigtopf-Feld `website`** sehen Menschen nicht. Ist es gefuellt,
 *   meldet die Route Erfolg und speichert nichts, damit ein Bot nicht lernt,
 *   woran er gescheitert ist.
 * - **Die Einwilligung ist Pflicht.** Ohne sie duerfen wir die Adresse nicht
 *   fuer die Benachrichtigung zum Start verwenden.
 */

export const ROLES = {
  schueler: "Schüler:in",
  eltern: "Elternteil",
  lehrkraft: "Lehrkraft",
  andere: "Etwas anderes",
} as const;

export const PLATFORMS = {
  windows: "Windows",
  macos: "macOS",
  server: "Eigener Server",
} as const;

export type Role = keyof typeof ROLES;
export type Platform = keyof typeof PLATFORMS;

export interface WaitlistEntry {
  email: string;
  name: string | null;
  role: Role | null;
  platform: Platform | null;
}

export type WaitlistField = "email" | "name" | "role" | "platform" | "consent" | "form";

export type ParseResult =
  | { ok: true; entry: WaitlistEntry; spam: boolean }
  | { ok: false; field: WaitlistField; message: string };

export const MAX_EMAIL_LENGTH = 254;
export const MAX_NAME_LENGTH = 80;

// Absichtlich schlicht: ein Zeichen vor dem @, eine Domain mit Punkt, keine
// Leerzeichen. Ob die Adresse wirklich existiert, zeigt erst die Mail zum Start.
const EMAIL_PATTERN = /^[^\s@]+@[^\s@.]+(\.[^\s@.]+)+$/;

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function choice<T extends string>(value: unknown, options: Record<T, string>): T | null | undefined {
  const raw = text(value);
  if (!raw) {
    return null;
  }
  return Object.prototype.hasOwnProperty.call(options, raw) ? (raw as T) : undefined;
}

export function parseWaitlistInput(input: unknown): ParseResult {
  if (!input || typeof input !== "object" || Array.isArray(input)) {
    return { ok: false, field: "form", message: "Die Anfrage war leer oder unlesbar." };
  }
  const data = input as Record<string, unknown>;

  const email = text(data.email).toLowerCase();
  if (!email) {
    return { ok: false, field: "email", message: "Bitte gib deine E-Mail-Adresse an." };
  }
  if (email.length > MAX_EMAIL_LENGTH || !EMAIL_PATTERN.test(email)) {
    return { ok: false, field: "email", message: "Diese E-Mail-Adresse sieht nicht gültig aus." };
  }

  const name = text(data.name).replace(/\s+/g, " ");
  if (name.length > MAX_NAME_LENGTH) {
    return { ok: false, field: "name", message: `Der Name darf höchstens ${MAX_NAME_LENGTH} Zeichen haben.` };
  }

  const role = choice(data.role, ROLES);
  if (role === undefined) {
    return { ok: false, field: "role", message: "Bitte wähle eine der angebotenen Rollen." };
  }

  const platform = choice(data.platform, PLATFORMS);
  if (platform === undefined) {
    return { ok: false, field: "platform", message: "Bitte wähle eine der angebotenen Plattformen." };
  }

  if (data.consent !== true && data.consent !== "on" && data.consent !== "true") {
    return {
      ok: false,
      field: "consent",
      message: "Bitte bestätige, dass wir dir zum Start eine E-Mail schicken dürfen.",
    };
  }

  return {
    ok: true,
    entry: { email, name: name || null, role, platform },
    spam: text(data.website).length > 0,
  };
}

/*
 * Mengenbegrenzung je Absender im Speicher des Prozesses.
 *
 * Kein Ersatz fuer eine Begrenzung am Rand (Proxy, Hoster): auf mehreren
 * Instanzen zaehlt jede fuer sich, und ein Neustart setzt alles zurueck. Sie
 * haelt aber ein Skript ab, das das Formular im Sekundentakt abschickt.
 */
export const RATE_LIMIT_WINDOW_MS = 10 * 60 * 1000;
export const RATE_LIMIT_MAX = 5;

export class RateLimiter {
  private readonly hits = new Map<string, number[]>();

  constructor(
    private readonly max = RATE_LIMIT_MAX,
    private readonly windowMs = RATE_LIMIT_WINDOW_MS,
  ) {}

  /** `true`, wenn der Aufruf erlaubt ist; zaehlt ihn dann auch. */
  allow(key: string, now = Date.now()): boolean {
    const since = now - this.windowMs;
    const recent = (this.hits.get(key) ?? []).filter((time) => time > since);
    if (recent.length >= this.max) {
      this.hits.set(key, recent);
      return false;
    }
    recent.push(now);
    this.hits.set(key, recent);
    if (this.hits.size > 10_000) {
      this.prune(since);
    }
    return true;
  }

  private prune(since: number) {
    for (const [key, times] of this.hits) {
      if (times.every((time) => time <= since)) {
        this.hits.delete(key);
      }
    }
  }
}
