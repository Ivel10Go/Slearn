import { afterEach, describe, expect, it } from "vitest";
import { ApiError } from "./api";
import { LOGIN_PATH, loginErrorMessage, loginHref, resolveAccess, safeNextPath, type SetupMode } from "./auth";
import { setLocaleForTests } from "./i18n";
import type { UserResponse } from "./types";

const user = { id: 1, username: "admin", email: null } as unknown as UserResponse;

function probes(mode: SetupMode, session: () => Promise<UserResponse | null>) {
  let sessionCalls = 0;
  return {
    probes: {
      setupMode: async () => mode,
      session: () => {
        sessionCalls += 1;
        return session();
      },
    },
    sessionCalls: () => sessionCalls,
  };
}

describe("resolveAccess", () => {
  it("fragt im lokalen Modus nie nach der Sitzung", async () => {
    const done = probes("done", async () => null);
    expect(await resolveAccess(done.probes)).toEqual({ state: "ready", serverMode: false });
    expect(done.sessionCalls()).toBe(0);

    const needed = probes("needed", async () => null);
    expect(await resolveAccess(needed.probes)).toEqual({ state: "setup", serverMode: false });
    expect(needed.sessionCalls()).toBe(0);
  });

  it("schickt im Server-Modus ohne Sitzung zur Anmeldung", async () => {
    expect(await resolveAccess(probes("auth", async () => null).probes)).toEqual({ state: "login", serverMode: true });
  });

  it("laesst im Server-Modus mit Sitzung durch", async () => {
    expect(await resolveAccess(probes("auth", async () => user).probes)).toEqual({ state: "ready", serverMode: true });
  });

  it("bleibt offen, wenn die Sitzung nicht abgefragt werden kann", async () => {
    const failing = probes("auth", async () => {
      throw new ApiError("Database unavailable", 503);
    });
    expect(await resolveAccess(failing.probes)).toEqual({ state: "ready", serverMode: true });
  });
});

describe("safeNextPath", () => {
  it("behaelt Pfade innerhalb von Slearn", () => {
    expect(safeNextPath("/lernen")).toBe("/lernen");
    expect(safeNextPath("/settings?tab=konto")).toBe("/settings?tab=konto");
  });

  it("verweigert fremde Herkuenfte und Unsinn", () => {
    for (const raw of [null, undefined, "", "lernen", "https://boese.example", "//boese.example", "/\\boese.example", "javascript:alert(1)"]) {
      expect(safeNextPath(raw)).toBe("/");
    }
  });

  it("fuehrt nicht zurueck auf die Anmeldeseite", () => {
    expect(safeNextPath(LOGIN_PATH)).toBe("/");
    expect(safeNextPath(`${LOGIN_PATH}?weiter=%2F`)).toBe("/");
  });
});

describe("loginHref", () => {
  it("haengt das Ziel nur an, wenn es nicht die Startseite ist", () => {
    expect(loginHref("/")).toBe(LOGIN_PATH);
    expect(loginHref(null)).toBe(LOGIN_PATH);
    expect(loginHref("/lernen?x=1")).toBe(`${LOGIN_PATH}?weiter=%2Flernen%3Fx%3D1`);
    expect(loginHref("//boese.example")).toBe(LOGIN_PATH);
  });
});

describe("loginErrorMessage", () => {
  afterEach(() => setLocaleForTests("de"));

  it("unterscheidet falsches Passwort und Rate-Limit", () => {
    expect(loginErrorMessage(new ApiError("Invalid credentials", 401))).toBe("Benutzername oder Passwort stimmt nicht.");
    expect(loginErrorMessage(new ApiError("Too many login attempts", 429))).toContain("Zu viele Anmeldeversuche");
  });

  it("zeigt die Verbindungsmeldung und sonst einen allgemeinen Satz", () => {
    expect(loginErrorMessage(new ApiError("Keine Verbindung", 0))).toBe("Keine Verbindung");
    expect(loginErrorMessage(new ApiError("Authentication is not configured", 503))).toBe("Die Anmeldung hat nicht geklappt. Bitte versuche es noch einmal.");
    expect(loginErrorMessage(new Error("kaputt"))).toBe("Die Anmeldung hat nicht geklappt. Bitte versuche es noch einmal.");
  });

  it("spricht Englisch, wenn die Oberflaeche es tut", () => {
    setLocaleForTests("en");
    expect(loginErrorMessage(new ApiError("Too many login attempts", 429))).toBe("Too many sign-in attempts. Wait a few minutes and then try again.");
  });
});
