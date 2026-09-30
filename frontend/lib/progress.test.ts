import { describe, expect, it } from "vitest";
import {
  balkenAnteil,
  faelligeThemen,
  intervallRegel,
  laufendeSessionHinweis,
  lernzeit,
  streakLabel,
  unBewerteteAnzahl,
  weekdayBalken,
  weekdayKurz,
  wiederholungLabel,
  wochenzielLabel,
} from "./progress";
import type { ConfidenceStep, TopicProgress, WeekdayStudyTime } from "./types";

function thema(overrides: Partial<TopicProgress> = {}): TopicProgress {
  return {
    topic_id: 1,
    name: "Ableitungen",
    subject_id: 2,
    subject_name: "Mathe",
    confidence: 0.5,
    confidence_step: 0.5,
    confidence_label: "sicher",
    last_reviewed_at: "2026-09-28T10:00:00Z",
    notes: null,
    interval_days: 7,
    next_review_on: "2026-10-05",
    days_until_review: 7,
    is_due: false,
    studied_minutes: 45,
    session_count: 2,
    updated_at: "2026-09-28T10:00:00Z",
    ...overrides,
  };
}

const WOCHENTAGE: WeekdayStudyTime[] = [
  { weekday: 0, label: "Montag", minutes: 60, sessions: 2 },
  { weekday: 1, label: "Dienstag", minutes: 0, sessions: 0 },
  { weekday: 2, label: "Mittwoch", minutes: 30, sessions: 1 },
  { weekday: 3, label: "Donnerstag", minutes: 90, sessions: 3 },
  { weekday: 4, label: "Freitag", minutes: 15, sessions: 1 },
  { weekday: 5, label: "Samstag", minutes: 0, sessions: 0 },
  { weekday: 6, label: "Sonntag", minutes: 45, sessions: 1 },
];

describe("lernzeit", () => {
  it("sagt bei null Minuten '0 Min.' und nicht 'Ohne Dauer'", () => {
    // `formatDuration(0)` sagt "Ohne Dauer" - das stimmt fuer eine Aufgabe,
    // fuer ein Protokoll aber nicht. Null gelernte Minuten sind ein Ergebnis.
    expect(lernzeit(0)).toBe("0 Min.");
    expect(lernzeit(45)).toBe("45 Min.");
    expect(lernzeit(90)).toBe("1 Std. 30 Min.");
  });
});

describe("balkenAnteil", () => {
  it("rechnet den Anteil am groessten Wert und bleibt in den Grenzen", () => {
    expect(balkenAnteil(30, 60)).toBe(50);
    expect(balkenAnteil(60, 60)).toBe(100);
    expect(balkenAnteil(90, 60)).toBe(100);
    expect(balkenAnteil(0, 60)).toBe(0);
    expect(balkenAnteil(30, 0)).toBe(0);
    expect(balkenAnteil(-5, 60)).toBe(0);
  });
});

describe("weekdayKurz", () => {
  it("schneidet den Wochentagsnamen auf zwei Zeichen", () => {
    expect(weekdayKurz("Montag")).toBe("Mo");
    expect(weekdayKurz("Mittwoch")).toBe("Mi");
    expect(weekdayKurz("Sonntag")).toBe("So");
  });
});

describe("weekdayBalken", () => {
  it("bezieht jeden Balken auf den groessten Wochentag und haelt alle sieben Tage", () => {
    const balken = weekdayBalken(WOCHENTAGE);
    expect(balken).toHaveLength(7);
    expect(balken[0].anteil).toBe(67); // 60 von 90
    expect(balken[3].anteil).toBe(100);
    expect(balken[0].maximum).toBe(90);
    // Ein leerer Tag ist 0 und kein fehlender Eintrag: eine Luecke ist eine
    // Aussage, und eine Balkenliste mit Luecken liest sich anders als eine
    // ohne.
    expect(balken[1]).toMatchObject({ label: "Dienstag", anteil: 0, maximum: 90 });
  });

  it("teilt nicht durch null, wenn noch nie gelernt wurde", () => {
    const leer = weekdayBalken(WOCHENTAGE.map((tag) => ({ ...tag, minutes: 0 })));
    expect(leer.every((tag) => tag.anteil === 0)).toBe(true);
  });
});

describe("wiederholungLabel", () => {
  it("nennt einen fehlenden Termin 'Noch nie wiederholt'", () => {
    // Ohne `last_reviewed_at` gibt es keinen Anker. "Heute" waere hier eine
    // Behauptung, die niemand belegen kann.
    expect(wiederholungLabel(thema({ next_review_on: null, days_until_review: null, interval_days: null }))).toBe("Noch nie wiederholt");
  });

  it("unterscheidet faellig, ueberfaellig und geplant", () => {
    expect(wiederholungLabel(thema({ days_until_review: 0, is_due: true }))).toBe("Heute wiederholen");
    expect(wiederholungLabel(thema({ days_until_review: 1 }))).toBe("Morgen wiederholen");
    expect(wiederholungLabel(thema({ days_until_review: 7 }))).toBe("In 7 Tagen");
    expect(wiederholungLabel(thema({ days_until_review: -1, is_due: true }))).toBe("Seit gestern fällig");
    expect(wiederholungLabel(thema({ days_until_review: -3, is_due: true }))).toBe("Seit 3 Tagen fällig");
  });

  it("faellt auf das Datum zurueck, wenn die Tageangabe fehlt", () => {
    expect(wiederholungLabel(thema({ days_until_review: null }))).toBe("Wiederholen am 2026-10-05");
  });
});

describe("streakLabel", () => {
  it("nennt keine Serie, wenn weder heute noch gestern gelernt wurde", () => {
    expect(streakLabel({ current_days: 0, studied_today: false })).toBe("Keine laufende Serie");
  });

  it("haelt fest, dass der heutige Tag noch offen ist", () => {
    // Wer morgens nachsieht, hat den Tag vor sich. Ohne diesen Zusatz wuerde
    // die Zahl fallen, ohne dass etwas passiert ist.
    expect(streakLabel({ current_days: 3, studied_today: false })).toBe("3 Tage · heute noch offen");
    expect(streakLabel({ current_days: 3, studied_today: true })).toBe("3 Tage in Folge");
  });

  it("bleibt bei einem Tag in der Einzahl", () => {
    expect(streakLabel({ current_days: 1, studied_today: true })).toBe("1 Tag in Folge");
    expect(streakLabel({ current_days: 1, studied_today: false })).toBe("1 Tag · heute noch offen");
  });
});

describe("wochenzielLabel", () => {
  it("stellt den Wochenstand seinem Ziel gegenueber", () => {
    expect(wochenzielLabel({ studied_minutes: 180, goal_minutes: 300 })).toBe("3 Std. von 5 Std.");
    expect(wochenzielLabel({ studied_minutes: 90, goal_minutes: 300 })).toBe("1 Std. 30 Min. von 5 Std.");
    expect(wochenzielLabel({ studied_minutes: 0, goal_minutes: 300 })).toBe("0 Min. von 5 Std.");
  });
});

describe("faelligeThemen und unBewerteteAnzahl", () => {
  const themen = [
    thema({ topic_id: 1, is_due: true, last_reviewed_at: "2026-09-20T10:00:00Z" }),
    thema({ topic_id: 2, is_due: false, last_reviewed_at: "2026-09-28T10:00:00Z" }),
    thema({ topic_id: 3, is_due: false, last_reviewed_at: null, next_review_on: null, days_until_review: null }),
  ];

  it("laesst die Sortierung des Backends stehen und filtert nur", () => {
    // Ein eigenes Sortieren waere eine zweite Wahrheit ueber dieselbe Frage.
    expect(faelligeThemen(themen).map((eintrag) => eintrag.topic_id)).toEqual([1]);
  });

  it("zaehlt die noch nie eingeschätzten Themen", () => {
    expect(unBewerteteAnzahl(themen)).toBe(1);
  });
});

describe("laufendeSessionHinweis", () => {
  it("schweigt, wenn keine Session laeuft", () => {
    expect(laufendeSessionHinweis({ open_sessions: 0, active_minutes: 12 })).toBeNull();
  });

  it("nennt die offene Session und sagt, dass ihre Zeit noch nicht zaehlt", () => {
    expect(laufendeSessionHinweis({ open_sessions: 1, active_minutes: 12 })).toBe(
      "1 Lernsession läuft noch · 12 Min. zählen erst nach dem Beenden.",
    );
    expect(laufendeSessionHinweis({ open_sessions: 3, active_minutes: 0 })).toContain("3 Lernsessions laufen noch");
  });
});

describe("intervallRegel", () => {
  const STUFEN: ConfidenceStep[] = [
    { value: 0, label: "unsicher", interval_days: 1 },
    { value: 0.25, label: "geübt", interval_days: 3 },
    { value: 0.5, label: "sicher", interval_days: 7 },
    { value: 0.75, label: "fest", interval_days: 14 },
    { value: 1, label: "gefestigt", interval_days: 30 },
  ];

  it("baut den Erklaerungstext aus der Stufentabelle des Backends", () => {
    const text = intervallRegel(STUFEN);
    // Bewusst aus der Tabelle und nicht festgetippt: sonst waere die
    // Erklaerung die zweite Wahrheit neben der Rechnung.
    expect(text).toContain("unsicher 1 Tag");
    expect(text).toContain("gefestigt 30 Tage");
    expect(text).toContain("Je sicherer die Einschätzung, desto länger bis zur nächsten Wiederholung.");
  });

  it("liefert ohne Stufen keinen Text, statt einen leeren Absatz zu zeigen", () => {
    expect(intervallRegel([])).toBe("");
  });
});
