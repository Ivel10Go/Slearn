/**
 * Fachfarben. Dieselbe Liste wie `SUBJECT_COLORS` in `backend/app/db/identity.py`:
 * ein neues Fach bekommt eine davon zufaellig, aendern laesst sie sich jederzeit.
 */
export const SUBJECT_COLORS = [
  "#E11D48", "#F97316", "#F59E0B", "#84CC16", "#22C55E", "#14B8A6",
  "#06B6D4", "#0EA5E9", "#3B82F6", "#6366F1", "#8B5CF6", "#A855F7",
  "#D946EF", "#EC4899", "#78716C", "#0F766E",
] as const;

/** Zufaellige Farbe, bevorzugt eine, die noch kein anderes Fach traegt. */
export function randomSubjectColor(taken: Iterable<string> = [], random: () => number = Math.random): string {
  const vergeben = new Set([...taken].map((farbe) => farbe.toUpperCase()));
  const frei = SUBJECT_COLORS.filter((farbe) => !vergeben.has(farbe));
  const auswahl = frei.length > 0 ? frei : SUBJECT_COLORS;
  return auswahl[Math.floor(random() * auswahl.length)] ?? SUBJECT_COLORS[0];
}
