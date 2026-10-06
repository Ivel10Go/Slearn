import { describe, expect, it } from "vitest";
import { bundleLessonChoices, type LessonChoiceGroup } from "./lesson-choices";

function gruppe(key: string, weekday: number, start: string, end: string, faecher: [number, string, boolean][], decided = false): LessonChoiceGroup {
  return {
    key,
    weekday,
    start,
    end,
    decided,
    options: faecher.map(([subject_id, name, selected]) => ({ subject_id, name, short_name: null, color: null, selected })),
  };
}

const nbtSpanisch = (selNbt: boolean, selSp: boolean): [number, string, boolean][] => [
  [7, "NBT", selNbt],
  [3, "Spanisch", selSp],
];

describe("bundleLessonChoices", () => {
  it("fasst eine Doppelstunde und weitere Tage mit denselben Fächern zu einer Frage zusammen", () => {
    const buendel = bundleLessonChoices([
      gruppe("a", 0, "07:45", "08:30", nbtSpanisch(true, true)),
      gruppe("b", 0, "08:30", "09:15", nbtSpanisch(true, true)),
      gruppe("c", 3, "09:35", "10:20", nbtSpanisch(true, true)),
      gruppe("d", 2, "07:45", "08:30", [[1, "Religion", true], [2, "Ethik", true]]),
    ]);
    expect(buendel).toHaveLength(2);
    const [erstes] = buendel;
    expect(erstes.keys).toEqual(["a", "b", "c"]);
    expect(erstes.windows).toEqual([
      { weekday: 0, start: "07:45", end: "09:15" },
      { weekday: 3, start: "09:35", end: "10:20" },
    ]);
    expect(erstes.decided).toBe(false);
    expect(erstes.selected).toEqual([]);
  });

  it("verschmilzt Stunden mit kurzer Pause, aber nicht über eine große Pause hinweg", () => {
    const [buendel] = bundleLessonChoices([
      gruppe("a", 1, "07:45", "08:30", nbtSpanisch(true, true)),
      gruppe("b", 1, "08:35", "09:20", nbtSpanisch(true, true)),
      gruppe("c", 1, "11:00", "11:45", nbtSpanisch(true, true)),
    ]);
    expect(buendel.windows).toEqual([
      { weekday: 1, start: "07:45", end: "09:20" },
      { weekday: 1, start: "11:00", end: "11:45" },
    ]);
  });

  it("übernimmt die Auswahl nur, wenn alle entschiedenen Gruppen sie teilen", () => {
    const [einig] = bundleLessonChoices([
      gruppe("a", 0, "07:45", "08:30", nbtSpanisch(false, true), true),
      gruppe("b", 0, "08:30", "09:15", nbtSpanisch(false, true), true),
    ]);
    expect(einig.decided).toBe(true);
    expect(einig.selected).toEqual([3]);

    const [teilweise] = bundleLessonChoices([
      gruppe("a", 0, "07:45", "08:30", nbtSpanisch(false, true), true),
      gruppe("b", 4, "07:45", "08:30", nbtSpanisch(true, true)),
    ]);
    expect(teilweise.decided).toBe(false);
    expect(teilweise.selected).toEqual([3]);
  });
});
