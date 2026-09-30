import { describe, expect, it } from "vitest";
import {
  BESTE_NOTE,
  GRADE_ART,
  aktuellesHalbjahr,
  bestandenLabel,
  gewichtText,
  grenzLabel,
  hochrechnungText,
  kopfZahl,
  notenAnzahl,
  notenText,
  notenZeile,
  schnittText,
  verlaufAchse,
  verlaufAltText,
  verlaufBeschriftung,
  verlaufPunkte,
  yFuer,
} from "./grades";
import type { GradeProjection, GradeSummary, GradeTrendPoint } from "./types";

/** `count` und `weight` kommen vom Backend **kumuliert**. */
function punkt(graded_on: string, average: number, count = 1): GradeTrendPoint {
  return { graded_on, average, count, weight: count };
}

const VERLAUF: GradeTrendPoint[] = [
  punkt("2026-09-07", 2.4, 1),
  punkt("2026-09-14", 2.0, 2),
  punkt("2026-09-28", 1.8, 3),
];

function hochrechnung(overrides: Partial<GradeProjection> = {}): GradeProjection {
  return {
    current_average: 2.07,
    passing_grade: 4,
    projected_weight: 2,
    needed_average: 4.0,
    best_possible_average: 1.57,
    still_possible: true,
    ...overrides,
  };
}

describe("Noten in Worten", () => {
  it("schreibt Zahlen mit Komma", () => {
    expect(notenText(1)).toBe("1,0");
    expect(notenText(2.5)).toBe("2,5");
    expect(notenText(4)).toBe("4,0");
  });

  it("unterscheidet bestanden von nicht bestanden - ohne dritte Stufe", () => {
    expect(bestandenLabel(true)).toBe("bestanden");
    expect(bestandenLabel(false)).toBe("nicht bestanden");
  });

  it("nennt jeden Schnitt und zeichnet keinen, den es nicht gibt", () => {
    expect(schnittText(3.25)).toBe("3,3");
    expect(schnittText(null)).toBe("—");
  });

  it("behandelt Singular und Mehrzahl", () => {
    expect(notenAnzahl(1)).toBe("1 Note");
    expect(notenAnzahl(4)).toBe("4 Noten");
  });

  it("nennt das Gewicht nur, wenn es vom Default abweicht", () => {
    expect(gewichtText(1)).toBeNull();
    expect(gewichtText(2)).toBe("zählt 2×");
  });

  it("kennt die drei Arten", () => {
    expect(GRADE_ART.written_exam).toBe("Klassenarbeit");
    expect(GRADE_ART.test).toBe("Test");
    expect(GRADE_ART.oral).toBe("Mündlich");
  });

  it("schreibt eine Zeile aus Note, Art, Gewicht und Kommentar", () => {
    expect(notenZeile({ grade_value: 1.7, kind: "test", weight: 2, comment: "viel Übung" })).toBe(
      "1,7 · Test · zählt 2× · viel Übung",
    );
    expect(notenZeile({ grade_value: 2, kind: "oral", weight: 1, comment: null })).toBe("2,0 · Mündlich");
  });

  it("zählt in der Kopfzeile - und verschweigt nichts", () => {
    const voll: GradeSummary = {
      passing_grade: 4,
      generated_at: "2026-09-30T10:00:00Z",
      overall: { subject_id: null, subject_name: "Alle Fächer", average: 2.13, count: 3, weight: 4, passed: true },
      by_subject: [],
      trend: VERLAUF,
      total_count: 3,
      projection: hochrechnung(),
    };
    expect(kopfZahl(voll)).toBe("3 Noten · Schnitt 2,1");
    expect(kopfZahl({ ...voll, total_count: 0, overall: { ...voll.overall, count: 0, average: 0 } })).toBe("Noch keine Noten");
  });
});

describe("Die Achse der Verlaufslinie", () => {
  it("deckt immer die Skala und die Bestehensgrenze ab", () => {
    // Gute Noten: die Achse beginnt trotzdem bei 1,0. Eine Achse, die mit dem
    // Schnitt auf 0,8 begönne, würde jeden Unterschied plattziehen.
    expect(verlaufAchse(VERLAUF, 4)).toEqual({ min: 1, max: 4 });
    expect(verlaufAchse([punkt("2026-09-07", 1.2), punkt("2026-09-14", 1.5)], 4)).toEqual({ min: 1, max: 4 });
  });

  it("wächst nach oben, wenn eine Note schlechter als die Grenze ist", () => {
    expect(verlaufAchse([punkt("2026-09-07", 4.6), punkt("2026-09-14", 4.4)], 4)).toEqual({ min: 1, max: 5 });
  });

  it("überlebt eine leere Liste", () => {
    expect(verlaufAchse([], 4)).toEqual({ min: 1, max: 4 });
  });

  it("setzt die beste Note nach oben", () => {
    expect(yFuer(1, 4, 40, BESTE_NOTE)).toBe(0);
    expect(yFuer(1, 4, 40, 4)).toBe(40);
    expect(yFuer(1, 4, 40, 2.5)).toBe(20);
  });

  it("rechnet auch dann, wenn min und max zusammenfallen", () => {
    // Sonst waere die Hoehe durch null geteilt und die Linie stuende nicht mehr.
    expect(Number.isFinite(yFuer(4, 4, 40, 4))).toBe(true);
  });
});

describe("Die Punkte der Linie", () => {
  it("verteilt die Tage, nicht die Noten", () => {
    const punkte = verlaufPunkte(VERLAUF, 100, 40, 1, 4);
    expect(punkte).toHaveLength(3);
    // Der erste Punkt ist am linken, der letzte am rechten Rand.
    expect(punkte[0].x).toBe(2);
    expect(punkte[2].x).toBe(98);
    // Der 14.09. liegt 7 von 21 Tagen nach dem ersten Punkt.
    expect(punkte[1].x).toBe(34);
  });

  it("liefert bei einem Punkt keine unbrauchbare Koordinate", () => {
    expect(verlaufPunkte([], 100, 40, 1, 4)).toEqual([]);
    const einzel = verlaufPunkte([punkt("2026-09-07", 2.0)], 100, 40, 1, 4);
    expect(einzel).toHaveLength(1);
    // Bei einem einzigen Punkt gibt es nichts zu verbinden; die Karte zeichnet
    // ihn als Punkt.
    expect(Number.isFinite(einzel[0].x)).toBe(true);
    expect(Number.isFinite(einzel[0].y)).toBe(true);
  });

  it("rundet auf zwei Stellen, damit die Punkte nicht flimmern", () => {
    const punkte = verlaufPunkte([punkt("2026-09-07", 2.333)], 100, 40, 1, 4);
    expect(punkte[0].y).toBe(Math.round(punkte[0].y * 100) / 100);
  });

  it("beschriftet den Verlauf mit Anzahl und Zeitraum", () => {
    expect(verlaufBeschriftung(VERLAUF)).toBe("3 Noten über 3 Wochen");
    expect(verlaufBeschriftung([punkt("2026-09-07", 2.0, 1), punkt("2026-09-11", 2.0, 2)])).toBe("2 Noten über 4 Tage");
    expect(verlaufBeschriftung([])).toBeNull();
  });
});

describe("Die Hochrechnung", () => {
  it("schweigt, wenn nichts aussteht", () => {
    expect(hochrechnungText(hochrechnung({ projected_weight: 0 }))).toBeNull();
  });

  it("nennt die Note, die ab jetzt nötig wäre", () => {
    expect(hochrechnungText(hochrechnung())).toBe("Ab 4,0 im Rest klappt die 4,0. Mit lauter 1,0 kämst du auf 1,6.");
  });

  it("sagt es klar, wenn die Grenze nicht mehr erreichbar ist", () => {
    // `needed_average` ist null, weil die nötige Note außerhalb der Skala liegt.
    // Eine Zahl zu erfinden wäre die schlimmere Antwort.
    expect(hochrechnungText(hochrechnung({ needed_average: null, still_possible: false }))).toBe(
      "Auch mit lauter 1,0 im Rest ist die 4,0 nicht mehr drin.",
    );
  });

  it("kommt ohne den besten Fall aus, wenn es ihn nicht gibt", () => {
    expect(hochrechnungText(hochrechnung({ best_possible_average: null }))).toBe("Ab 4,0 im Rest klappt die 4,0.");
  });

  it("nennt die Schwelle auf der Linie", () => {
    expect(grenzLabel(4)).toBe("4,0 · bestanden ab hier");
  });

  it("erzählt der Linie Anfang, Ende und Grenze", () => {
    // Die Grafik ist ein Bild; ohne diese Zahlen waere sie eine Behauptung.
    expect(verlaufAltText(VERLAUF, 4)).toBe(
      "Schnittverlauf: 2,4 am 2026-09-07 bis 1,8 am 2026-09-28, aus 3 Noten. Bestehensgrenze 4,0.",
    );
    expect(verlaufAltText([punkt("2026-09-07", 2.0)], 4)).toBe("Eine Note: 2,0 am 2026-09-07. Bestehensgrenze 4,0.");
    expect(verlaufAltText([], 4)).toBe("Noch kein Notenverlauf.");
  });
});

describe("Das Halbjahr", () => {
  it("beginnt im Februar und im August", () => {
    expect(aktuellesHalbjahr(new Date(2026, 8, 30))).toEqual({ start: "2026-08-01", label: "2026 bis Januar" });
    expect(aktuellesHalbjahr(new Date(2026, 1, 3))).toEqual({ start: "2026-02-01", label: "2026 bis August" });
  });
});
