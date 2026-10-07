import { afterEach, describe, expect, it } from "vitest";
import { describeUpdateStatus, getDesktopBridge, toDesktopTheme } from "./desktop";

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

describe("Update-Stand", () => {
  it("sagt nichts, wenn die App keine Updates kann", () => {
    expect(describeUpdateStatus(null)).toBe("");
    expect(describeUpdateStatus({ state: "idle", supported: false, appVersion: "1.0.0" })).toBe("");
  });

  it("nennt die Version eines geladenen Updates", () => {
    expect(describeUpdateStatus({ state: "downloaded", supported: true, version: "1.2.0", appVersion: "1.1.0" })).toBe(
      "Version 1.2.0 ist bereit und wird beim nächsten Neustart installiert.",
    );
  });

  it("zeigt die Fehlermeldung des Updaters", () => {
    expect(
      describeUpdateStatus({ state: "error", supported: true, appVersion: "1.1.0", message: "Keine Verbindung zum Update-Server." }),
    ).toBe("Keine Verbindung zum Update-Server.");
  });
});
