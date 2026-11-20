import { describe, expect, it } from "vitest";
import { mindmapLines } from "./notebook";

describe("mindmapLines", () => {
  it("rechnet Einrückung und Aufzählungszeichen in Tiefe um", () => {
    expect(mindmapLines("Ableitungen\n- Regeln\n  - Kettenregel\n\n- Anwendung")).toEqual([
      { depth: 0, text: "Ableitungen" },
      { depth: 1, text: "Regeln" },
      { depth: 2, text: "Kettenregel" },
      { depth: 1, text: "Anwendung" },
    ]);
  });

  it("nimmt Überschriften-Rauten weg", () => {
    expect(mindmapLines("# Thema")).toEqual([{ depth: 0, text: "Thema" }]);
  });
});
