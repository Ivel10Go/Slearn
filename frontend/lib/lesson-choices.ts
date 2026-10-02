import { apiRequest } from "@/lib/api";

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
