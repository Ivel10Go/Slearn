import { describe, expect, it } from "vitest";
import { isNavigationHrefActive, isSectionActive, normalizeHash, sectionOf } from "./navigation";

describe("Bereiche mit Reitern", () => {
  it("haelt den Navigationspunkt auf allen Reitern seines Bereichs aktiv", () => {
    expect(isSectionActive("/klassenarbeiten", "/lernen")).toBe(true);
    expect(isSectionActive("/fortschritt", "/lernen")).toBe(true);
    expect(isSectionActive("/faecher", "/dokumente")).toBe(true);
    expect(isSectionActive("/ki", "/settings")).toBe(true);
    expect(isSectionActive("/ki", "/lernen")).toBe(false);
    expect(isSectionActive("/assistent", "/assistent")).toBe(true);
  });

  it("kennt fuer Seiten ohne Reiter keinen Bereich", () => {
    expect(sectionOf("/assistent")).toBeUndefined();
    expect(sectionOf("/")).toBeUndefined();
  });
});

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
