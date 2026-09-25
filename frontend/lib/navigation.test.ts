import { describe, expect, it } from "vitest";
import { isNavigationHrefActive, normalizeHash } from "./navigation";

describe("Hash-Navigation", () => {
  it("normalisiert Hashwerte", () => {
    expect(normalizeHash("#study-tasks")).toBe("study-tasks");
    expect(normalizeHash("calendar")).toBe("calendar");
  });

  it("markiert den zur aktuellen URL gehörenden Eintrag", () => {
    expect(isNavigationHrefActive("/", "/#overview", "")).toBe(true);
    expect(isNavigationHrefActive("/", "/#schedule", "#schedule")).toBe(true);
    expect(isNavigationHrefActive("/", "/#study-tasks", "#calendar")).toBe(false);
    expect(isNavigationHrefActive("/login", "/#overview", "")).toBe(false);
  });
});
