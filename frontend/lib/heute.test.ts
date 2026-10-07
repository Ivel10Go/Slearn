import { describe, expect, it } from "vitest";
import { agentVorschlaege, fachName, heutePunkte, wannText } from "./heute";
import type { DashboardData } from "./types";

const JETZT = new Date(2026, 9, 7, 9, 0);

function daten(teil: Partial<DashboardData> = {}): DashboardData {
  return {
    lessons: [],
    upcomingExams: [],
    homework: [],
    studyTasks: [],
    calendarEvents: [],
    summary: {} as DashboardData["summary"],
    ...teil,
  };
}

function stunde(subject: string, start: Date, ende: Date, isCancelled = false) {
  return {
    id: subject,
    subject,
    subjectShort: null,
    title: subject,
    teacher: "",
    room: "A1",
    startTime: start.toISOString(),
    endTime: ende.toISOString(),
    color: "",
    isCancelled,
  };
}

function hausaufgabe(subject: string, title: string, faellig: Date, completed = false) {
  return { id: title, subject, title, description: "", dueDate: faellig.toISOString(), completed };
}

describe("wannText", () => {
  it("nennt heute, morgen und Abstaende in Tagen", () => {
    expect(wannText(new Date(2026, 9, 7, 23, 0), JETZT)).toBe("heute");
    expect(wannText(new Date(2026, 9, 8, 1, 0), JETZT)).toBe("morgen");
    expect(wannText(new Date(2026, 9, 11), JETZT)).toBe("in 4 Tagen");
    expect(wannText(new Date(2026, 9, 5), JETZT)).toBe("seit 2 Tagen");
  });
});

describe("heutePunkte", () => {
  it("zeigt die laufende Stunde, ueberspringt ausgefallene und nennt die naechste Hausaufgabe", () => {
    const punkte = heutePunkte(
      daten({
        lessons: [
          stunde("Mathe", new Date(2026, 9, 7, 8, 30), new Date(2026, 9, 7, 9, 15)),
          stunde("Sport", new Date(2026, 9, 7, 9, 20), new Date(2026, 9, 7, 10, 5), true),
        ],
        homework: [
          hausaufgabe("Bio", "Zellzyklus", new Date(2026, 9, 9)),
          hausaufgabe("Deutsch", "Aufsatz", new Date(2026, 9, 8)),
          hausaufgabe("Englisch", "Vokabeln", new Date(2026, 9, 8), true),
        ],
      }),
      JETZT,
    );
    expect(punkte[0].label).toBe("Jetzt");
    expect(punkte[0].text).toContain("Mathe bis");
    expect(punkte[1].text).toBe("Deutsch: Aufsatz, fällig morgen (+1 weitere)");
    expect(punkte).toHaveLength(2);
  });

  it("sagt ehrlich, wenn nichts mehr ansteht", () => {
    const punkte = heutePunkte(daten(), JETZT);
    expect(punkte.map((punkt) => punkt.text)).toEqual(["Heute keine Stunde mehr", "Alles erledigt"]);
  });
});

describe("agentVorschlaege", () => {
  it("schlaegt Wochenplan, Abfrage und Abhaken aus den Daten vor", () => {
    const vorschlaege = agentVorschlaege(
      daten({
        homework: [hausaufgabe("Bio", "Zellzyklus", new Date(2026, 9, 9))],
        upcomingExams: [
          { id: "1", subject: "Mathe", title: "Klausur", date: new Date(2026, 9, 12).toISOString(), description: "", topics: [], progress: null },
        ],
      }),
      JETZT,
    );
    expect(vorschlaege).toEqual(["Plan meine Woche", "Frag mich ab für Mathe", "Bio erledigt", "Was steht diese Woche an?"]);
  });

  it("ohne offene Sachen gibt es keinen Wochenplan-Vorschlag", () => {
    expect(agentVorschlaege(daten(), JETZT)).not.toContain("Plan meine Woche");
  });
});

describe("fachName", () => {
  it("behandelt den Platzhalter des Backends als kein Fach", () => {
    expect(fachName("Unbekannt")).toBe("");
    expect(fachName("Unbekanntes Fach")).toBe("");
    expect(fachName(" Bio ")).toBe("Bio");
    expect(agentVorschlaege(daten({ homework: [hausaufgabe("Unbekannt", "Aufsatz", new Date(2026, 9, 9))] }), JETZT)).toContain("Aufsatz erledigt");
  });
});
