import { describe, expect, it } from "vitest";
import { randomSubjectColor, SUBJECT_COLORS } from "./subject-colors";

describe("randomSubjectColor", () => {
  it("wählt eine Farbe aus der Palette", () => {
    expect(SUBJECT_COLORS).toContain(randomSubjectColor());
  });

  it("bevorzugt Farben, die noch kein Fach trägt", () => {
    const vergeben = SUBJECT_COLORS.slice(1).map((farbe) => farbe.toLowerCase());
    expect(randomSubjectColor(vergeben, () => 0.99)).toBe(SUBJECT_COLORS[0]);
  });

  it("nimmt wieder alle Farben, wenn jede vergeben ist", () => {
    expect(SUBJECT_COLORS).toContain(randomSubjectColor(SUBJECT_COLORS, () => 0.5));
  });
});
