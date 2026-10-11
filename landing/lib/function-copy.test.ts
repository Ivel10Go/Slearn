import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

// Die Edge Function nimmt nur Dateien aus ihrem Ordner mit und traegt deshalb
// eine Kopie der Regeln. Sie darf nicht von `lib/waitlist.ts` abweichen.
describe("Edge Function der Warteliste", () => {
  it("nutzt dieselben Regeln wie lib/waitlist.ts", () => {
    const original = readFileSync(path.join(__dirname, "waitlist.ts"), "utf8");
    const kopie = readFileSync(path.join(__dirname, "..", "supabase", "functions", "waitlist", "waitlist.ts"), "utf8");
    expect(kopie.replace(/\r\n/g, "\n")).toBe(original.replace(/\r\n/g, "\n"));
  });
});
