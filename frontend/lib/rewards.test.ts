import { describe, expect, it } from "vitest";
import { parseRewardOverview } from "./schemas";
import {
  badgeAnteil,
  badgeBereichLabel,
  badgeBilanz,
  badgeFortschrittLabel,
  badgeHaufen,
  badgeReihenfolge,
  balkenAnteil,
  entryArtLabel,
  entryDatumLabel,
  entryPunkteLabel,
  nullPunktHinweis,
  offeneBadges,
  punkteLabel,
  regelSatz,
  wochenLabel,
} from "./rewards";
import type { RewardBadge, RewardOverview, RewardRules, RewardStep } from "./types";

const REGELN: RewardRules = {
  task_points: 15,
  day_cap: 80,
  subject_day_cap: 40,
  min_subject_day_minutes: 10,
  subject_day_steps: [
    { minutes: 10, points: 5 },
    { minutes: 25, points: 15 },
    { minutes: 50, points: 30 },
  ],
};

const STUFEN: RewardStep[] = REGELN.subject_day_steps;

function badge(teil: Partial<RewardBadge>): RewardBadge {
  return {
    id: 1,
    code: "beispiel",
    title: "Beispiel",
    description: "Eine Bedingung.",
    scope: "global",
    scope_kind: "global",
    subject_id: null,
    subject_name: null,
    topic_id: null,
    topic_name: null,
    progress: 0,
    target: 3,
    awarded: false,
    awarded_at: null,
    repeatable: false,
    unit: "Tag",
    unit_plural: "Tagen",
    ...teil,
  };
}

describe("Belohnungskarte", () => {
  describe("Punkte und Obergrenze", () => {
    it("nennt die Tagesobergrenze mit", () => {
      expect(punkteLabel({ today_points: 15, today_cap: 80 })).toBe("15 von 80 Punkten");
    });

    it("zeigt auch 0 ohne Ausreden", () => {
      expect(punkteLabel({ today_points: 0, today_cap: 80 })).toBe("0 von 80 Punkten");
    });

    it("rechnet einen Balkenanteil nur zwischen 0 und 100", () => {
      expect(balkenAnteil(15, 80)).toBe(19);
      expect(balkenAnteil(200, 80)).toBe(100);
      expect(balkenAnteil(-5, 80)).toBe(0);
      expect(balkenAnteil(5, 0)).toBe(0);
    });

    it("erklaert eine Buchung ohne Punkte als Tageskonto, nicht als Fehler", () => {
      expect(nullPunktHinweis(REGELN)).toContain("80");
      expect(nullPunktHinweis(REGELN)).toContain("gelernt");
    });
  });

  describe("Auszeichnungen", () => {
    it("misst den Fortschritt am Ziel, nicht an sich selbst", () => {
      expect(badgeAnteil(badge({ progress: 1, target: 3 }))).toBe(33);
      expect(badgeAnteil(badge({ progress: 3, target: 3 }))).toBe(100);
      expect(badgeAnteil(badge({ progress: 0, target: 3 }))).toBe(0);
    });

    it("nennt Fortschritt und Ziel in deutschen Worten", () => {
      expect(badgeFortschrittLabel(badge({ progress: 2, target: 5 }))).toBe("2 von 5 Tagen");
      expect(badgeFortschrittLabel(badge({ progress: 5, target: 5, awarded: true }))).toBe("Erreicht · 5 Tagen");
    });

    it("nutzt die Einheit aus dem Katalog statt einer eigenen Formulierung", () => {
      expect(badgeFortschrittLabel(badge({ progress: 1, target: 3, unit: "Minute", unit_plural: "Minuten" }))).toBe("1 von 3 Minuten");
      expect(badgeFortschrittLabel(badge({ progress: 1, target: 2, unit: "Aufgabe", unit_plural: "Aufgaben" }))).toBe("1 von 2 Aufgaben");
    });

    it("bleibt bei der Einzahl in der Einzahl", () => {
      // "1 Aufgabe" statt "1 Aufgaben" - die Form kommt aus dem Katalog, damit
      // das Frontend sie nicht erraten muss.
      expect(badgeFortschrittLabel(badge({ progress: 1, target: 1, unit: "Aufgabe", unit_plural: "Aufgaben" }))).toBe("1 von 1 Aufgabe");
      expect(badgeFortschrittLabel(badge({ progress: 1, target: 1, unit: "Aufgabe", unit_plural: "Aufgaben", awarded: true }))).toBe("Erreicht · 1 Aufgabe");
    });

    it("nennt den Bereich so, wie man ihn wiedererkennt", () => {
      expect(badgeBereichLabel(badge({ scope_kind: "global", scope: "global" }))).toBe("Gesamt");
      expect(badgeBereichLabel(badge({ scope_kind: "subject", scope: "subject:7", subject_name: "Mathematik" }))).toBe("Mathematik");
      expect(badgeBereichLabel(badge({ scope_kind: "topic", scope: "topic:12", topic_name: "Integralrechnung" }))).toBe("Integralrechnung");
      expect(badgeBereichLabel(badge({ scope_kind: "week", scope: "week:2026-W40" }))).toBe("KW 40 · 2026");
    });

    it("faellt auf einen lesbaren Bereich zurueck, wenn der Name fehlt", () => {
      expect(badgeBereichLabel(badge({ scope_kind: "subject", scope: "subject:7" }))).toBe("Fach");
      expect(badgeBereichLabel(badge({ scope_kind: "topic", scope: "topic:12" }))).toBe("Thema");
      expect(badgeBereichLabel(badge({ scope_kind: "week", scope: "week:ungueltig" }))).toBe("Kalenderwoche");
    });

    it("stellt die Erreichten voran und danach den groessten Fortschritt", () => {
      const sortiert = badgeReihenfolge([
        badge({ id: 1, code: "offen_wenig", progress: 1, target: 5 }),
        badge({ id: 2, code: "offen_viel", progress: 4, target: 5 }),
        badge({ id: 3, code: "erreicht", progress: 3, target: 3, awarded: true }),
      ]);
      expect(sortiert.map((eintrag) => eintrag.id)).toEqual([3, 2, 1]);
    });

    it("sortiert bei gleichem Fortschritt stabil nach dem Code", () => {
      const sortiert = badgeReihenfolge([
        badge({ id: 1, code: "b", progress: 1, target: 5 }),
        badge({ id: 2, code: "a", progress: 1, target: 5 }),
      ]);
      expect(sortiert.map((eintrag) => eintrag.code)).toEqual(["a", "b"]);
    });

    it("teilt die Liste in verdient und offen", () => {
      const haufen = badgeHaufen([
        badge({ id: 1, code: "offen", progress: 1, target: 5 }),
        badge({ id: 2, code: "erreicht", progress: 3, target: 3, awarded: true }),
      ]);
      expect(haufen.erreicht.map((eintrag) => eintrag.code)).toEqual(["erreicht"]);
      expect(haufen.offen.map((eintrag) => eintrag.code)).toEqual(["offen"]);
    });

    it("zeigt nur die naechsten offenen Auszeichnungen", () => {
      const viele = Array.from({ length: 7 }, (_wert, index) =>
        badge({ id: index + 1, code: `offen_${index}`, progress: index, target: 10 }),
      );
      expect(offeneBadges(viele, 3)).toHaveLength(3);
      // Der groesste Fortschritt steht vorn.
      expect(offeneBadges(viele, 3)[0].code).toBe("offen_6");
    });

    it("zaehlt die moeglichen Auszeichnungen aus dem Katalog, nicht aus den Zeilen", () => {
      const overview = {
        catalog: [
          { code: "a", title: "A", description: "", target: 1, unit: "Aufgabe", unit_plural: "Aufgaben", scope_kind: "global" as const, repeatable: false },
          { code: "b", title: "B", description: "", target: 1, unit: "Tag", unit_plural: "Tagen", scope_kind: "global" as const, repeatable: false },
          { code: "c", title: "C", description: "", target: 1, unit: "Tag", unit_plural: "Tagen", scope_kind: "week" as const, repeatable: true },
        ],
        badges: [badge({ code: "a", awarded: true, progress: 1, target: 1 })],
      } as Pick<RewardOverview, "catalog" | "badges">;
      const bilanz = badgeBilanz(overview);
      // Eine erreichte von drei moeglichen - nicht "eine von einer".
      expect(bilanz.erreicht).toBe(1);
      expect(bilanz.moeglich).toBe(3);
    });
  });

  describe("Buchungen", () => {
    it("unterscheidet die beiden Arten in deutschen Worten", () => {
      expect(entryArtLabel({ kind: "task_completed" })).toBe("Aufgabe");
      expect(entryArtLabel({ kind: "subject_day" })).toBe("Fachtag");
    });

    it("schreibt Einzahl und Mehrzahl richtig", () => {
      expect(entryPunkteLabel(0)).toBe("0 Punkte");
      expect(entryPunkteLabel(1)).toBe("1 Punkt");
      expect(entryPunkteLabel(15)).toBe("15 Punkte");
    });

    it("zeigt ein Datum ohne Uhrzeit-Anteil", () => {
      expect(entryDatumLabel("2026-09-30")).toContain("2026");
      expect(entryDatumLabel("2026-09-30")).not.toContain("T12:00");
    });
  });

  describe("die Regel", () => {
    it("wird aus den Zahlen des Backends erzeugt, nicht festgetippt", () => {
      const satz = regelSatz(REGELN, STUFEN);
      expect(satz).toContain("15 Punkte");
      expect(satz).toContain("10 Min. 5 P.");
      expect(satz).toContain("25 Min. 15 P.");
      expect(satz).toContain("50 Min. 30 P.");
      expect(satz).toContain("40 Punkte pro Fach und Tag");
      expect(satz).toContain("80 Punkte");
      expect(satz).toContain("10 Minuten");
    });

    it("aendert sich mit den Zahlen des Backends", () => {
      const strenger = regelSatz({ ...REGELN, day_cap: 40, task_points: 20 }, STUFEN);
      expect(strenger).toContain("20 Punkte");
      expect(strenger).toContain("höchstens 40 Punkte;");
    });

    it("sagt, wohin Punkte nicht gehoeren", () => {
      const satz = regelSatz(REGELN, STUFEN);
      expect(satz).toContain("nicht in Noten");
      expect(satz).toContain("Priorität");
      expect(satz).toContain("KI-Dienst");
    });

    it("verlangt kein Dringlichkeitswort", () => {
      // "Level", "Stufe", "Ziel erreicht!" und "noch fehlen" waeren Druck. Die
      // Karte darf einen Fortschritt zeigen, aber kein Versprechen.
      const satz = regelSatz(REGELN, STUFEN).toLowerCase();
      for (const verboten of ["level", "stufe", "noch fehlen", "nur noch", "schnell", "streak"]) {
        expect(satz).not.toContain(verboten);
      }
    });
  });

  it("nennt die Woche mit ihrem Ende", () => {
    expect(wochenLabel({ week_points: 40, week_ends_on: "2026-10-04" })).toContain("40 Punkte");
    expect(wochenLabel({ week_points: 40, week_ends_on: "2026-10-04" })).toContain("2026");
  });
});

/**
 * Die Antwortform des Endpunkts.
 *
 * Der Test liest eine vollstaendige Antwort aus `/v1/rewards` ein - so, wie das
 * Backend sie schickt. Er ist die Haelfte einer Zwei-Wege-Zusage: auf der
 * Backend-Seite `test_die_belohnungsantwort_nennt_die_felder_der_oberflaeche`, auf
 * dieser hier das `parse`-Gegenteil. Benennt eines der beiden ein Feld um, faellt
 * eines der beiden auf - und nicht erst ein Typfehler zur Laufzeit.
 */
describe("Die Antwort von /v1/rewards", () => {
  const ANTWORT = {
    summary: {
      generated_at: "2026-09-30T14:04:24Z",
      timezone: "Europe/Berlin",
      today_points: 30,
      today_cap: 80,
      week_points: 105,
      total_points: 310,
      today: "2026-09-30",
      week_starts_on: "2026-09-28",
      week_ends_on: "2026-10-04",
      awarded_badges: 2,
      pending_badges: 1,
    },
    rules: {
      task_points: 15,
      day_cap: 80,
      subject_day_cap: 40,
      min_subject_day_minutes: 10,
      subject_day_steps: [
        { minutes: 10, points: 5 },
        { minutes: 25, points: 15 },
        { minutes: 50, points: 30 },
      ],
    },
    catalog: [
      { code: "erster_abschluss", title: "Erster Abschluss", description: "…", target: 1, unit: "Aufgabe", unit_plural: "Aufgaben", scope_kind: "global", repeatable: false },
    ],
    badges: [
      {
        id: 1,
        code: "regelmaessig_wiederholt",
        title: "Regelmäßig wiederholt",
        description: "…",
        scope: "topic:12",
        scope_kind: "topic",
        subject_id: null,
        subject_name: null,
        topic_id: 12,
        topic_name: "Integralrechnung",
        progress: 3,
        target: 3,
        awarded: true,
        awarded_at: "2026-09-30T13:00:00Z",
        repeatable: false,
        unit: "Tag",
        unit_plural: "Tagen",
      },
    ],
    entries: [
      {
        id: 9,
        kind: "subject_day",
        points: 0,
        minutes: 30,
        subject_id: 3,
        subject_name: "Mathematik",
        topic_id: null,
        topic_name: null,
        detail: "30 Min. Mathematik am 30.09.2026",
        earned_on: "2026-09-30",
        earned_at: "2026-09-30T14:00:00Z",
      },
    ],
  };

  it("liest Konto, Regeln, Katalog, Auszeichnungen und Buchungen", () => {
    const uebersicht = parseRewardOverview(ANTWORT);
    expect(uebersicht.summary.today_cap).toBe(80);
    expect(uebersicht.rules.subject_day_steps).toHaveLength(3);
    expect(uebersicht.catalog).toHaveLength(1);
    expect(uebersicht.badges[0].awarded).toBe(true);
    expect(uebersicht.entries[0].earned_on).toBe("2026-09-30");
  });

  it("behaelt eine Buchung mit 0 Punkten - sie ist eine Aussage, kein Fehler", () => {
    // Ohne `min(0)` wuerde die Zeile hier fallen, und die Karte wuerde einen
    // gelernten Tag ohne jede Spur verschwinden lassen, sobald das Tageskonto
    // gefuellt ist.
    const uebersicht = parseRewardOverview(ANTWORT);
    expect(uebersicht.entries[0].points).toBe(0);
    expect(uebersicht.entries[0].minutes).toBe(30);
    expect(entryPunkteLabel(uebersicht.entries[0].points)).toBe("0 Punkte");
  });

  it("macht aus fehlenden Angaben ausdruecklich null", () => {
    const ohne_namen = parseRewardOverview({
      ...ANTWORT,
      badges: [{ ...ANTWORT.badges[0], subject_id: null, subject_name: null, topic_id: null, topic_name: null, awarded_at: null, awarded: false }],
    });
    expect(ohne_namen.badges[0].subject_name).toBeNull();
    expect(ohne_namen.badges[0].topic_name).toBeNull();
    expect(ohne_namen.badges[0].awarded_at).toBeNull();
    expect(ohne_namen.badges[0].awarded).toBe(false);
    // Und faellt auf einen lesbaren Bereich zurueck statt auf eine leere Zeile.
    expect(badgeBereichLabel(ohne_namen.badges[0])).toBe("Thema");
  });

  it("weist eine Antwort ohne Regel zurueck", () => {
    // Die Regel ist kein Beiwerk: ohne sie kann die Karte ihren erklärenden Satz
    // nicht bauen, und ein hartcodierter waere die zweite Wahrheit.
    const ohne_regel = { ...ANTWORT };
    delete (ohne_regel as Partial<typeof ANTWORT>).rules;
    expect(() => parseRewardOverview(ohne_regel)).toThrow();
  });

  it("weist eine unbekannte Buchungsart zurueck", () => {
    // Die Art steht auch als Check-Constraint in der Migration; eine dritte
    // waere eine Regel, fuer die es keine Punkte gibt.
    const mit_erfundener_art = {
      ...ANTWORT,
      entries: [{ ...ANTWORT.entries[0], kind: "streak_bonus" }],
    };
    expect(() => parseRewardOverview(mit_erfundener_art)).toThrow();
  });
});
