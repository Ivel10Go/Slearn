import { afterEach, describe, expect, it } from "vitest";
import { getDesktopBridge, toDesktopTheme } from "./desktop";

describe("Desktop-Bruecke", () => {
  afterEach(() => {
    delete (globalThis as { window?: unknown }).window;
  });

  it("fehlt ohne Fenster", () => {
    expect(getDesktopBridge()).toBeNull();
  });

  it("fehlt im Browser ohne Preload", () => {
    (globalThis as { window?: unknown }).window = {};
    expect(getDesktopBridge()).toBeNull();
  });

  it("liefert die Bruecke aus dem Preload", () => {
    const bridge = { platform: "darwin", version: "1.0.0" };
    (globalThis as { window?: unknown }).window = { lernraumDesktop: bridge };
    expect(getDesktopBridge()).toBe(bridge);
  });

  it("bildet unbekannte Themes auf system ab", () => {
    expect(toDesktopTheme("dark")).toBe("dark");
    expect(toDesktopTheme("light")).toBe("light");
    expect(toDesktopTheme("system")).toBe("system");
    expect(toDesktopTheme(undefined)).toBe("system");
    expect(toDesktopTheme("sepia")).toBe("system");
  });
});
