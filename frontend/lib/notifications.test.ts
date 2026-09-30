import { describe, expect, it } from "vitest";
import {
  aktiveKanaele,
  ankerFuer,
  base64urlToBytes,
  bezeichnung,
  beschreibeRuhezeit,
  bytesToBase64url,
  darfLokalAnzeigen,
  pruefknopfMoeglich,
  pushVerfuegbar,
  type NotificationSettings,
  type QuietHours,
} from "./notifications";

const RUHE_NACHT: QuietHours = { enabled: true, start: "22:00", end: "07:00", spans_midnight: true };

function einstellungen(teil: Partial<NotificationSettings> = {}): NotificationSettings {
  return {
    enabled: true,
    channels: { study_tasks: true, exams: false },
    channel_labels: { study_tasks: "Lernaufgaben", exams: "Prüfungen" },
    channels_available: ["study_tasks", "exams"],
    reminder_time: "17:00",
    quiet_hours: RUHE_NACHT,
    exam_lead_days: 7,
    timezone: "Europe/Berlin",
    preference_key: "notifications",
    summary: "Lernaufgaben um 17:00 Uhr",
    ...teil,
  };
}

describe("Der Schluessel des Push-Abonnements", () => {
  it("rechnet base64url in Bytes um, ohne Polster und mit den URL-Zeichen", () => {
    // Ein 65 Byte langer Punkt auf P-256: 0x04 und zwei 32-Byte-Zahlen.
    const schluessel = bytesToBase64url(
      new Uint8Array([0x04, 0xfb, 0xff, 0x80, 0x01, 0xbf, 0x02, 0x3e]),
    );
    expect(schluessel).not.toContain("+");
    expect(schluessel).not.toContain("/");
    expect(schluessel).not.toContain("=");
    expect(Array.from(base64urlToBytes(schluessel))).toEqual([0x04, 0xfb, 0xff, 0x80, 0x01, 0xbf, 0x02, 0x3e]);
  });

  it("kommt mit einer Laenge aus, die nicht durch vier teilbar ist", () => {
    // Ohne Polster waere die Laenge 3 Byte -> 4 Zeichen ohne "=", 1 Byte -> 2.
    const drei = bytesToBase64url(new Uint8Array([1, 2, 3]));
    const eins = bytesToBase64url(new Uint8Array([255]));
    expect(drei).toBe("AQID");
    expect(eins).toBe("_w");
    expect(Array.from(base64urlToBytes(drei))).toEqual([1, 2, 3]);
    expect(Array.from(base64urlToBytes(eins))).toEqual([255]);
  });
});

describe("Der lokale Weg", () => {
  it("zeigt nur, wenn der Browser erlaubt hat und der Tab sichtbar ist", () => {
    expect(darfLokalAnzeigen("granted", false)).toBe(true);
    expect(darfLokalAnzeigen("denied", false)).toBe(false);
    expect(darfLokalAnzeigen("default", false)).toBe(false);
    expect(darfLokalAnzeigen(undefined, false)).toBe(false);
    // Im Hintergrund zeigt der Browser eine Benachrichtigung nicht an.
    expect(darfLokalAnzeigen("granted", true)).toBe(false);
  });
});

describe("Die Anker", () => {
  it("fuehren zu den Karten der Einzelseite", () => {
    expect(ankerFuer("study_task_due")).toBe("/#study-tasks");
    expect(ankerFuer("exam_due")).toBe("/#calendar");
    expect(bezeichnung("study_task_due")).toBe("Lernaufgabe");
    expect(bezeichnung("exam_due")).toBe("Prüfung");
  });
});

describe("Push im Browser", () => {
  it("bricht ab, wenn eine der drei Voraussetzungen fehlt", () => {
    expect(pushVerfuegbar({ serviceWorker: {}, PushManager: {}, Notification: {} })).toBe(true);
    // Firefox mit deaktivierter Push-Unterstuetzung hat keinen PushManager.
    expect(pushVerfuegbar({ serviceWorker: {}, Notification: {} })).toBe(false);
    expect(pushVerfuegbar({ PushManager: {}, Notification: {} })).toBe(false);
    expect(pushVerfuegbar({ serviceWorker: {}, PushManager: {} })).toBe(false);
    expect(pushVerfuegbar(undefined)).toBe(false);
  });
});

describe("Die Ruhezeit", () => {
  it("sagt, wenn sie ueber Mitternacht laeuft", () => {
    expect(beschreibeRuhezeit(RUHE_NACHT)).toBe("22:00 bis 07:00 (über Mitternacht)");
    expect(beschreibeRuhezeit({ enabled: true, start: "13:00", end: "15:00", spans_midnight: false })).toBe(
      "13:00 bis 15:00",
    );
    expect(beschreibeRuhezeit({ enabled: false, start: "22:00", end: "07:00", spans_midnight: true })).toBe("aus");
  });
});

describe("Der Knopf in den Einstellungen", () => {
  it("ist nur bereit, wenn wirklich etwas entstehen kann", () => {
    expect(pruefknopfMoeglich(einstellungen())).toBe(true);
    expect(pruefknopfMoeglich(einstellungen({ enabled: false }))).toBe(false);
    expect(pruefknopfMoeglich(einstellungen({ channels: { study_tasks: false, exams: false } }))).toBe(false);
    expect(pruefknopfMoeglich(null)).toBe(false);
  });

  it("zaehlt nur die Kanaele auf, die der Server auch kennt", () => {
    expect(aktiveKanaele(einstellungen())).toEqual(["study_tasks"]);
    expect(aktiveKanaele(einstellungen({ channels: { study_tasks: true, exams: true } }))).toEqual([
      "study_tasks",
      "exams",
    ]);
    expect(aktiveKanaele(null)).toEqual([]);
  });
});
