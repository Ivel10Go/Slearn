import { apiRequest } from "@/lib/api";

/** Eine wiederkehrende Stunde der Woche (`weekday`: 0 = Montag). */
export interface TimetableEntryDraft {
  weekday: number;
  start: string;
  end: string;
  subject: string;
  short_name: string | null;
  room: string | null;
}

export interface TimetableSubjectDraft {
  name: string;
  short_name: string | null;
  exists: boolean;
}

export interface TimetableExtraction {
  entries: TimetableEntryDraft[];
  subjects: TimetableSubjectDraft[];
  via: "image" | "pdf_text";
}

export interface TimetableApplied {
  created_subjects: number;
  imported_lessons: number;
  window_start: string;
  window_end: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

/**
 * Prueft die Antwort von Hand: die Zod-Schemas in `lib/schemas.ts` sind zum
 * Teil Stubs, und ein falsch gelesener Plan soll hier als Fehler enden und
 * nicht als leere Vorschau.
 */
export function parseExtraction(payload: unknown): TimetableExtraction {
  if (!isRecord(payload) || !Array.isArray(payload.entries) || !Array.isArray(payload.subjects)) {
    throw new Error("Der Server hat den Plan in einem unerwarteten Format beantwortet.");
  }
  const entries: TimetableEntryDraft[] = [];
  for (const raw of payload.entries) {
    if (!isRecord(raw) || typeof raw.weekday !== "number") continue;
    const subject = text(raw.subject);
    const start = text(raw.start);
    const end = text(raw.end);
    if (!subject || !start || !end) continue;
    entries.push({ weekday: raw.weekday, start, end, subject, short_name: text(raw.short_name), room: text(raw.room) });
  }
  const subjects: TimetableSubjectDraft[] = [];
  for (const raw of payload.subjects) {
    if (!isRecord(raw)) continue;
    const name = text(raw.name);
    if (name) subjects.push({ name, short_name: text(raw.short_name), exists: raw.exists === true });
  }
  return { entries, subjects, via: payload.via === "pdf_text" ? "pdf_text" : "image" };
}

export function parseApplied(payload: unknown): TimetableApplied {
  if (!isRecord(payload) || typeof payload.imported_lessons !== "number" || typeof payload.created_subjects !== "number") {
    throw new Error("Der Server hat die Übernahme in einem unerwarteten Format beantwortet.");
  }
  return {
    created_subjects: payload.created_subjects,
    imported_lessons: payload.imported_lessons,
    window_start: String(payload.window_start ?? ""),
    window_end: String(payload.window_end ?? ""),
  };
}

/** Liest einen Plan aus PDF oder Bild aus. Schreibt nichts in die Datenbank. */
export async function extractTimetable(file: File): Promise<TimetableExtraction> {
  const form = new FormData();
  form.append("file", file);
  return parseExtraction(await apiRequest("/v1/timetable/extract", { method: "POST", body: form }));
}

/** Uebernimmt den (ggf. korrigierten) Vorschlag: legt Faecher und Stunden an. */
export async function applyTimetable(entries: TimetableEntryDraft[], weeks = 8): Promise<TimetableApplied> {
  return parseApplied(
    await apiRequest("/v1/timetable/apply", { method: "POST", body: JSON.stringify({ entries, weeks }) }),
  );
}
