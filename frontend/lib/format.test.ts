import { describe, expect, it } from "vitest";
import { formatDueDate, getCountdown, getDayProgress, getLessonState } from "./format";
import { normalizeDashboard, type DashboardApiResponse } from "./types";

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
