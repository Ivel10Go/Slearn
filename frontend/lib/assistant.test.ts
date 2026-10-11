import { describe, expect, it } from "vitest";
import { bestaetigung, describeProposalTime, historyForRequest, parseRouteKey, routeKey } from "./assistant";

describe("describeProposalTime", () => {
  it("zeigt eine Prüfung mit Uhrzeit in Ortszeit", () => {
    const text = describeProposalTime({ kind: "exam", starts_at: "2026-10-09T06:00:00Z", ends_at: null, all_day: false }, "Europe/Berlin");
    expect(text).toContain("09.10.2026");
    expect(text).toContain("08:00");
  });

  it("zeigt einen ganztägigen Termin ohne Uhrzeit", () => {
    const text = describeProposalTime({ kind: "event", starts_at: "2026-10-08T22:00:00Z", ends_at: "2026-10-09T22:00:00Z", all_day: true }, "Europe/Berlin");
    expect(text).toContain("ganztägig");
    expect(text).toContain("09.10.2026");
  });

  it("zeigt bei einem Termin Beginn und Ende", () => {
    const text = describeProposalTime({ kind: "event", starts_at: "2026-10-09T13:30:00Z", ends_at: "2026-10-09T14:30:00Z", all_day: false }, "Europe/Berlin");
    expect(text).toContain("15:30–16:30");
  });

  it("markiert Hausaufgaben als fällig", () => {
    expect(describeProposalTime({ kind: "homework", starts_at: "2026-10-09T06:00:00Z", ends_at: null, all_day: false }, "Europe/Berlin")).toMatch(/^fällig /);
  });

  it("meldet ein unlesbares Datum", () => {
    expect(describeProposalTime({ kind: "exam", starts_at: "kaputt", ends_at: null, all_day: false })).toBe("Datum unklar");
  });
});

describe("Modellauswahl", () => {
  it("unterscheidet geladene Modelle und eingetragene Verbindungen", () => {
    expect(routeKey({ integration_id: null, local_model_id: "qwen", name: "Q", model: "qwen", source: "local", selected: true })).toBe("local:qwen");
    expect(routeKey({ integration_id: 3, local_model_id: null, name: "O", model: "x", source: "local", selected: false })).toBe("int:3");
    expect(parseRouteKey("local:qwen")).toEqual({ kind: "local", modelId: "qwen" });
    expect(parseRouteKey("int:3")).toEqual({ kind: "integration", id: 3 });
    expect(parseRouteKey("auto")).toEqual({ kind: "auto" });
    expect(parseRouteKey("int:abc")).toEqual({ kind: "auto" });
  });
});

describe("bestaetigung", () => {
  it("erkennt kurze Zustimmung und Ablehnung", () => {
    expect(bestaetigung("Ja.")).toBe("ja");
    expect(bestaetigung("Ja bitte, trag es ein!")).toBe("ja");
    expect(bestaetigung("Okay passt")).toBe("ja");
    expect(bestaetigung("Nein, lieber nicht.")).toBe("nein");
    expect(bestaetigung("Nee")).toBe("nein");
  });

  it("versteht auch englische Zustimmung und Ablehnung", () => {
    expect(bestaetigung("Yes, please.")).toBe("ja");
    expect(bestaetigung("Sure, go ahead")).toBe("ja");
    expect(bestaetigung("No thanks")).toBe("nein");
    expect(bestaetigung("Don't add it")).toBe("nein");
    expect(bestaetigung("Nothing else?")).toBeNull();
  });

  it("laesst neue Fragen an das Modell durch", () => {
    expect(bestaetigung("Ja, und was ist eigentlich mit der Mathe-Hausaufgabe für morgen?")).toBeNull();
    expect(bestaetigung("Was steht morgen an?")).toBeNull();
    expect(bestaetigung("")).toBeNull();
  });
});

describe("historyForRequest", () => {
  it("lässt leere Nachrichten weg und kürzt auf die letzten Züge", () => {
    const verlauf = Array.from({ length: 15 }, (_, index) => ({ role: index % 2 ? ("assistant" as const) : ("user" as const), content: `n${index}` }));
    verlauf.push({ role: "user", content: "  " });
    const ergebnis = historyForRequest(verlauf, 12);
    expect(ergebnis).toHaveLength(12);
    expect(ergebnis.at(-1)?.content).toBe("n14");
  });
});
