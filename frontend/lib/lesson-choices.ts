import { apiRequest } from "./api";

export interface LessonChoiceOption {
  subject_id: number;
  name: string;
  short_name: string | null;
  selected: boolean;
}

/** Mehrere Faecher zur selben Zeit; `weekday` 0 = Montag. */
export interface LessonChoiceGroup {
  key: string;
  weekday: number;
  start: string;
  end: string;
  decided: boolean;
  options: LessonChoiceOption[];
}

export interface LessonChoiceSaved {
  groups: LessonChoiceGroup[];
  resynced: boolean;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function parseGroups(payload: unknown): LessonChoiceGroup[] {
  if (!Array.isArray(payload)) {
    throw new Error("Der Server hat die Parallelstunden in einem unerwarteten Format beantwortet.");
  }
  const groups: LessonChoiceGroup[] = [];
  for (const raw of payload) {
    if (!isRecord(raw) || typeof raw.key !== "string" || typeof raw.weekday !== "number" || !Array.isArray(raw.options)) continue;
    const options: LessonChoiceOption[] = [];
    for (const option of raw.options) {
      if (!isRecord(option) || typeof option.subject_id !== "number" || typeof option.name !== "string") continue;
      options.push({
        subject_id: option.subject_id,
        name: option.name,
        short_name: typeof option.short_name === "string" ? option.short_name : null,
        selected: option.selected === true,
      });
    }
    groups.push({
      key: raw.key,
      weekday: raw.weekday,
      start: String(raw.start ?? ""),
      end: String(raw.end ?? ""),
      decided: raw.decided === true,
      options,
    });
  }
  return groups;
}

export async function getLessonChoices(): Promise<LessonChoiceGroup[]> {
  return parseGroups(await apiRequest("/v1/timetable/conflicts"));
}

/** `attended` bildet den Gruppenschluessel auf die besuchten Fach-IDs ab. */
export async function saveLessonChoices(attended: Record<string, number[]>): Promise<LessonChoiceSaved> {
  const selections = Object.entries(attended).map(([key, ids]) => ({ key, attended: ids }));
  const payload = await apiRequest("/v1/timetable/choices", { method: "PUT", body: JSON.stringify({ selections }) });
  if (!isRecord(payload)) {
    throw new Error("Der Server hat die Auswahl in einem unerwarteten Format beantwortet.");
  }
  return { groups: parseGroups(payload.groups), resynced: payload.resynced === true };
}

/** Ein zusammenhaengendes Zeitfenster; benachbarte Stunden sind schon verschmolzen. */
export interface LessonChoiceWindow {
  weekday: number;
  start: string;
  end: string;
}

/**
 * Alle Parallelstunden mit denselben Faechern - die eine Frage „NBT oder
 * Spanisch?“, egal ob die Faecher als Doppelstunde oder an mehreren Tagen
 * nebeneinander liegen. Der Server liefert je Stunde und Wochentag eine Gruppe.
 */
export interface LessonChoiceBundle {
  id: string;
  keys: string[];
  options: Omit<LessonChoiceOption, "selected">[];
  windows: LessonChoiceWindow[];
  /** Besuchte Faecher - nur aus bereits entschiedenen Gruppen, sonst leer. */
  selected: number[];
  decided: boolean;
}

/** Bis zu dieser Luecke (Minuten) gelten zwei Stunden als eine Doppelstunde. */
const MAX_PAUSE_MINUTES = 15;

function minuten(text: string): number {
  const [stunden, min] = text.split(":").map(Number);
  return (stunden ?? 0) * 60 + (min ?? 0);
}

function verschmelzen(fenster: LessonChoiceWindow[]): LessonChoiceWindow[] {
  const sortiert = [...fenster].sort((a, b) => a.weekday - b.weekday || minuten(a.start) - minuten(b.start));
  const ergebnis: LessonChoiceWindow[] = [];
  for (const eintrag of sortiert) {
    const letztes = ergebnis.at(-1);
    if (letztes && letztes.weekday === eintrag.weekday && minuten(eintrag.start) - minuten(letztes.end) <= MAX_PAUSE_MINUTES) {
      if (minuten(eintrag.end) > minuten(letztes.end)) letztes.end = eintrag.end;
    } else {
      ergebnis.push({ ...eintrag });
    }
  }
  return ergebnis;
}

export function bundleLessonChoices(groups: LessonChoiceGroup[]): LessonChoiceBundle[] {
  const buendel = new Map<string, { groups: LessonChoiceGroup[] }>();
  for (const group of groups) {
    const id = group.options.map((o) => o.subject_id).sort((a, b) => a - b).join(",");
    const eintrag = buendel.get(id) ?? { groups: [] };
    eintrag.groups.push(group);
    buendel.set(id, eintrag);
  }
  return [...buendel.entries()].map(([id, { groups: teil }]) => {
    const entschieden = teil.filter((g) => g.decided);
    // Nur was in jeder entschiedenen Gruppe besucht wird, gilt fuer das ganze Buendel.
    const selected = entschieden.length
      ? teil[0].options
          .map((o) => o.subject_id)
          .filter((sid) => entschieden.every((g) => g.options.some((o) => o.subject_id === sid && o.selected)))
      : [];
    return {
      id,
      keys: teil.map((g) => g.key),
      options: teil[0].options.map(({ subject_id, name, short_name }) => ({ subject_id, name, short_name })),
      windows: verschmelzen(teil.map(({ weekday, start, end }) => ({ weekday, start, end }))),
      selected,
      decided: entschieden.length === teil.length,
    };
  });
}
