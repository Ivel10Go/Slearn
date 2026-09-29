import { describe, expect, it } from "vitest";
import { ApiError } from "./api";
import { appendSorted, errorMessage, mergeById } from "./use-collection";

interface Zeile {
  id: number;
  titel: string;
}

const nachDatum = (a: Zeile, b: Zeile) => a.id - b.id;

describe("mergeById", () => {
  it("ersetzt genau das Element mit derselben Id", () => {
    const items: Zeile[] = [
      { id: 1, titel: "alt" },
      { id: 2, titel: "unberuehrt" },
    ];
    expect(mergeById(items, { id: 1, titel: "neu" })).toEqual([
      { id: 1, titel: "neu" },
      { id: 2, titel: "unberuehrt" },
    ]);
  });

  it("behaelt die Reihenfolge, statt neu zu sortieren", () => {
    // Die Sortierung kommt vom Backend (`ORDER BY due_date`). Ein Neuladen
    // nach einer Aktion wuerde eine gerade eingefuegte Zeile einsortieren und
    // die Liste springen lassen - das ist genau der Sprung, den die UI meidet.
    const items: Zeile[] = [
      { id: 9, titel: "spaeter" },
      { id: 1, titel: "frueher" },
    ];
    expect(mergeById(items, { id: 1, titel: "geaendert" }).map((item) => item.id)).toEqual([9, 1]);
  });

  it("haengt an, wenn die Id noch nicht in der Liste stand", () => {
    const items: Zeile[] = [{ id: 1, titel: "a" }];
    expect(mergeById(items, { id: 2, titel: "b" })).toHaveLength(2);
  });

  it("veraendert die uebergebene Liste nicht", () => {
    const items: Zeile[] = [{ id: 1, titel: "alt" }];
    mergeById(items, { id: 1, titel: "neu" });
    expect(items[0].titel).toBe("alt");
  });
});

describe("appendSorted", () => {
  it("haengt an und sortiert nach der uebergebenen Reihenfolge", () => {
    const items: Zeile[] = [
      { id: 3, titel: "c" },
      { id: 1, titel: "a" },
    ];
    expect(appendSorted(items, { id: 2, titel: "b" }, nachDatum).map((item) => item.id)).toEqual([1, 2, 3]);
  });
});

describe("errorMessage", () => {
  it("nennt bei 401 den Grund, statt das Backend-Wort zu zeigen", () => {
    // "Not authenticated" sagt einer Lernenden nichts. Der Hinweis auf die
    // abgelaufene Anmeldung schon.
    expect(errorMessage(new ApiError("Not authenticated", 401))).toContain("erneut an");
  });

  it("gibt die Backend-Meldung bei anderen Fehlern weiter", () => {
    expect(errorMessage(new ApiError("Fach existiert bereits", 409))).toBe("Fach existiert bereits");
  });

  it("faellt auf eine eigene Vorgabe zurueck", () => {
    expect(errorMessage(new Error(""), "eigener Text")).toBe("eigener Text");
    expect(errorMessage({}, "eigener Text")).toBe("eigener Text");
  });
});
