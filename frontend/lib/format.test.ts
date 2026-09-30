import { describe, expect, it } from "vitest";
import {
  addDays,
  addWeeks,
  eachDay,
  formatDateRange,
  formatDueDate,
  formatWeekNumber,
  getCountdown,
  getDayProgress,
  getLessonState,
  groupLessonSlots,
  isSameDay,
  pickSlotLesson,
  startOfDay,
  startOfWeek,
  toIsoDate,
} from "./format";
import { normalizeDashboard, normalizeGreeting, normalizeScheduleRange, type DashboardApiResponse } from "./types";

describe("Dashboard-Normalisierung", () => {
  it("wandelt snake_case in ein stabiles UI-Modell um", () => {
    const payload: DashboardApiResponse = {
      lessons: [
        {
          id: 7,
          subject_name: "Mathe",
          title: "Analysis",
          teacher_name: "Frau Berger",
          room_name: "204",
          start_time: "2026-09-25T08:00:00.000Z",
          end_time: "2026-09-25T08:50:00.000Z",
        },
      ],
      upcoming_exams: [
        {
          id: 8,
          subject_name: "Chemie",
          title: "Bindungen",
          date: "2026-09-30T09:00:00.000Z",
          topics: ["Ionen", "Metalle"],
          progress: 0.5,
        },
      ],
      homework: [
        {
          id: 9,
          subject_name: "Deutsch",
          title: "Text lesen",
          due_date: "2026-09-25T18:00:00.000Z",
          completed: false,
        },
      ],
      study_tasks: [
        {
          id: 10,
          subject_name: "Mathe",
          title: "Ableitungen",
          duration_minutes: 25,
          status: "in-progress",
          progress: 25,
        },
      ],
      calendar_events: [
        {
          id: 11,
          title: "Lerngruppe",
          start_time: "2026-09-25T16:00:00.000Z",
          end_time: "2026-09-25T17:00:00.000Z",
        },
      ],
      summary: {
        lessons_completed: 1,
        lessons_total: 1,
        tasks_completed: 0,
        tasks_total: 1,
        homework_pending: 1,
        upcoming_exams: 1,
        day_progress: 0.5,
      },
    };

    const result = normalizeDashboard(payload);

    expect(result.lessons[0]).toMatchObject({
      id: "7",
      subject: "Mathe",
      teacher: "Frau Berger",
      room: "204",
      startTime: "2026-09-25T08:00:00.000Z",
    });
    expect(result.upcomingExams[0].progress).toBe(50);
    expect(result.studyTasks[0].status).toBe("in_progress");
    expect(result.summary.dayProgress).toBe(50);
  });
});

describe("Datums- und Lernformatierung", () => {
  it("berechnet Countdown und überfällige Termine", () => {
    const now = new Date("2026-09-25T10:00:00.000Z");
    const upcoming = getCountdown("2026-09-27T10:00:00.000Z", now);
    const past = getCountdown("2026-09-24T10:00:00.000Z", now);

    expect(upcoming.days).toBe(2);
    expect(upcoming.label).toBe("in 2 Tagen");
    expect(past.isOverdue).toBe(true);
    expect(past.label).toBe("Vor 1 Tag");
  });

  it("erkennt aktuelle und beendete Lektionen", () => {
    const now = new Date("2026-09-25T10:15:00.000Z");
    const lesson = {
      startTime: "2026-09-25T10:00:00.000Z",
      endTime: "2026-09-25T10:50:00.000Z",
    };

    expect(getLessonState(lesson, now)).toBe("current");
    expect(getLessonState({ ...lesson, startTime: "2026-09-25T09:00:00.000Z", endTime: "2026-09-25T09:50:00.000Z" }, now)).toBe("completed");
    expect(getLessonState({ ...lesson, startTime: "2026-09-25T11:00:00.000Z", endTime: "2026-09-25T11:50:00.000Z" }, now)).toBe("upcoming");
  });

  it("berechnet Tagesfortschritt und Fälligkeiten", () => {
    const now = new Date("2026-09-25T12:00:00.000Z");
    const lessons = [
      { startTime: "2026-09-25T10:00:00.000Z", endTime: "2026-09-25T14:00:00.000Z" },
    ];

    expect(getDayProgress(lessons, now)).toBe(50);
    expect(formatDueDate("2026-09-25T18:00:00.000Z", now)).toBe("Heute fällig");
    expect(formatDueDate("2026-09-24T18:00:00.000Z", now)).toBe("Überfällig");
  });
});

describe("Stundenplan-Zeiträume", () => {
  // 2026-09-25 ist ein Freitag, 2026-09-28 ein Montag.
  const freitag = new Date(2026, 8, 25, 13, 45);

  it("schneidet den Tag und den Montag der Woche ab", () => {
    expect(startOfDay(freitag).getHours()).toBe(0);
    expect(startOfDay(freitag).getDate()).toBe(25);
    expect(toIsoDate(startOfWeek(freitag))).toBe("2026-09-21");
    // Montag bleibt Montag, Sonntag gehört noch zur laufenden Woche.
    expect(toIsoDate(startOfWeek(new Date(2026, 8, 27)))).toBe("2026-09-21");
    expect(toIsoDate(startOfWeek(new Date(2026, 8, 28)))).toBe("2026-09-28");
  });

  it("verändert das übergebene Datum nicht", () => {
    const original = new Date(2026, 8, 25, 13, 45);
    const before = original.getTime();

    startOfDay(original);
    addDays(original, 3);
    addWeeks(original, 1);
    startOfWeek(original);

    expect(original.getTime()).toBe(before);
  });

  it("rechnet in Tagen und Wochen weiter", () => {
    expect(toIsoDate(addDays(freitag, 3))).toBe("2026-09-28");
    expect(toIsoDate(addDays(freitag, -4))).toBe("2026-09-21");
    expect(toIsoDate(addWeeks(freitag, 1))).toBe("2026-10-02");
  });

  it("liefert jeden Kalendertag inklusive beider Grenzen", () => {
    expect(eachDay(new Date(2026, 8, 25), new Date(2026, 8, 27)).map(toIsoDate)).toEqual([
      "2026-09-25",
      "2026-09-26",
      "2026-09-27",
    ]);
    expect(eachDay(new Date(2026, 8, 25), new Date(2026, 8, 25))).toHaveLength(1);
  });

  it("vergleicht Tage unabhängig von der Uhrzeit", () => {
    expect(isSameDay(new Date(2026, 8, 25, 0, 1), new Date(2026, 8, 25, 23, 59))).toBe(true);
    expect(isSameDay(new Date(2026, 8, 25), new Date(2026, 8, 26))).toBe(false);
    expect(isSameDay(null, new Date(2026, 8, 25))).toBe(false);
  });

  it("nummeriert die Woche nach ISO-8601", () => {
    expect(formatWeekNumber(new Date(2026, 0, 4))).toBe("KW 1");
    // 4. Januar 2026 ist ein Sonntag und gehört damit noch zu KW 1.
    expect(formatWeekNumber(new Date(2026, 0, 5))).toBe("KW 2");
    expect(formatWeekNumber(new Date(2026, 8, 21))).toBe("KW 39");
  });

  it("beschriftet Zeiträume je nach Spanne", () => {
    expect(formatDateRange(new Date(2026, 8, 25), new Date(2026, 8, 27))).toContain("September");
    expect(formatDateRange(new Date(2026, 8, 25), new Date(2026, 8, 25))).toContain("September");
  });

  it("normalisiert die Zeitraum-Antwort und fängt fehlende Felder ab", () => {
    const result = normalizeScheduleRange({
      range_start: "2026-09-21",
      range_end: "2026-09-27",
      lessons: [
        {
          id: 3,
          subject_name: "Deutsch",
          teacher_name: "Herr Weber",
          room_name: "112",
          start_time: "2026-09-21T08:00:00.000Z",
          end_time: "2026-09-21T08:50:00.000Z",
        },
      ],
    });

    expect(result.rangeStart).toBe("2026-09-21");
    expect(result.rangeEnd).toBe("2026-09-27");
    expect(result.lessons[0]).toMatchObject({ id: "3", subject: "Deutsch", teacher: "Herr Weber", room: "112" });

    const kaputt = normalizeScheduleRange({ lessons: "keine Liste" });
    expect(kaputt.lessons).toEqual([]);
    expect(kaputt.rangeStart).toBeNull();
  });
});

describe("Parallel liegende Fächer", () => {
  // Echtes Muster aus dem Portal: 20260928 14:50 Deutsch oder Französisch.
  const fr = "2026-09-28T12:50:00.000Z";
  const frEnde = "2026-09-28T13:35:00.000Z";
  const drei = "2026-09-28T13:20:00.000Z";
  const dreiEnde = "2026-09-28T14:05:00.000Z";

  const f = { id: "1", startTime: fr, endTime: frEnde, subjectShort: "F", subject: "Französisch", room: "201" };
  const l = { id: "2", startTime: fr, endTime: frEnde, subjectShort: "L", subject: "Latein", room: "203" };
  const eR = { id: "3", startTime: drei, endTime: dreiEnde, subjectShort: "eR", subject: "ev. Religion", room: "102" };
  const kR = { id: "4", startTime: drei, endTime: dreiEnde, subjectShort: "kR", subject: "kath. Religion", room: "110" };
  const eth = { id: "5", startTime: drei, endTime: dreiEnde, subjectShort: "eth", subject: "Ethik", room: "323" };

  it("fasst Fächer mit gleicher Zeit zu einer Lage zusammen", () => {
    const slots = groupLessonSlots([f, l, eR, kR, eth]);

    expect(slots).toHaveLength(2);
    expect(slots[0].lessons).toHaveLength(2);
    expect(slots[0].lessons.map((l) => l.subjectShort)).toEqual(["F", "L"]);
    // Auch dreifach belegte Lagen bleiben eine Lage.
    expect(slots[1].lessons).toHaveLength(3);
  });

  it("sortiert Lagen chronologisch", () => {
    const slots = groupLessonSlots([eth, l, eR, f]);
    expect(slots[0].lessons[0].subjectShort).toBe("F");
    expect(slots[1].lessons[0].subjectShort).toBe("eR");
  });

  it("gibt den Schlüssel als Zeitlage wieder", () => {
    const [slot] = groupLessonSlots([f]);
    expect(slot.key).toBe(`${fr}|${frEnde}`);
  });

  it("liefert jede einzelne Stunde als eigene Lage", () => {
    expect(groupLessonSlots([f])[0].lessons).toHaveLength(1);
    expect(groupLessonSlots([])).toEqual([]);
  });

  it("wählt das gewünschte Fach einer Lage", () => {
    expect(pickSlotLesson([f, l], "2").id).toBe("2");
    expect(pickSlotLesson([f, l], null).id).toBe("1");
  });

  it("fällt auf die erste Alternative zurück, wenn die Wahl nicht mehr existiert", () => {
    // Nach einem Planwechsel zeigt die gespeicherte ID ins Leere.
    expect(pickSlotLesson([f, l], "99").id).toBe("1");
  });
});

describe("Willkommenssatz", () => {
  it("übernimmt Text, Herkunft und Modell", () => {
    const greeting = normalizeGreeting({
      text: "Herzlich willkommen, um 8:00 beginnt Mathematik.",
      source: "local",
      model: "qwen2.5",
      generated_on: "2026-09-25",
    });

    expect(greeting.text).toBe("Herzlich willkommen, um 8:00 beginnt Mathematik.");
    expect(greeting.source).toBe("local");
    expect(greeting.model).toBe("qwen2.5");
    expect(greeting.generatedOn).toBe("2026-09-25");
  });

  it("liest den gespeicherten Altwert 'ai' als Online-Provider", () => {
    // Ein Cacheeintrag von gestern darf nicht als Datenluege erscheinen.
    expect(normalizeGreeting({ text: "Hallo", source: "ai" }).source).toBe("provider");
    expect(normalizeGreeting({ text: "Hallo", source: "provider" }).source).toBe("provider");
  });

  it("behandelt unbekannte Herkunft als Daten-Satz", () => {
    // So behauptet die Oberfläche nie eine KI, die nicht geantwortet hat - und
    // erst recht keine *lokale*, weil das eine Datenschutzentscheidung wäre.
    expect(normalizeGreeting({ text: "Hallo", source: "irgendwas" }).source).toBe("fallback");
    expect(normalizeGreeting({ text: "Hallo" }).source).toBe("fallback");
    // `none` gibt es beim Willkommenssatz nicht.
    expect(normalizeGreeting({ text: "Hallo", source: "none" }).source).toBe("fallback");
  });

  it("übersteht eine Antwort ohne Text", () => {
    const greeting = normalizeGreeting({ source: "local" });
    expect(greeting.text.length).toBeGreaterThan(0);
    expect(greeting.model).toBeNull();
  });
});

describe("Kürzel und Entfall aus der API", () => {
  it("übernimmt Kürzel und Ausfall-Markierung", () => {
    const result = normalizeScheduleRange({
      range_start: "2026-09-21",
      range_end: "2026-09-27",
      lessons: [
        {
          id: 1,
          subject_name: "Französisch",
          subject_short_name: "F",
          is_cancelled: true,
          start_time: "2026-09-28T12:50:00.000Z",
          end_time: "2026-09-28T13:35:00.000Z",
        },
      ],
    });

    expect(result.lessons[0].subject).toBe("Französisch");
    expect(result.lessons[0].subjectShort).toBe("F");
    expect(result.lessons[0].isCancelled).toBe(true);
  });

  it("nutzt den ausgeschriebenen Namen, wenn das Portal kein Kürzel liefert", () => {
    const result = normalizeScheduleRange({
      lessons: [{ id: 2, subject_name: "Mathematik", start_time: "2026-09-28T08:00:00.000Z", end_time: "2026-09-28T08:50:00.000Z" }],
    });

    expect(result.lessons[0].subjectShort).toBe("Mathematik");
    expect(result.lessons[0].isCancelled).toBe(false);
  });
});
