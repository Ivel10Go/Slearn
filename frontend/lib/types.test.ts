import { describe, expect, it } from "vitest";
import { isDemoTaskId, normalizeDashboard, normalizeStudyAction } from "./types";

describe("Study-Normalisierung", () => {
  it("normalisiert eine Backend-Session mit Startzeit", () => {
    const result = normalizeStudyAction({
      id: 42,
      task_id: 7,
      started_at: "2026-09-25T10:00:00.000Z",
      duration_minutes: 25,
    }, 7);

    expect(result.session).toMatchObject({
      id: "42",
      taskId: "7",
      state: "active",
      durationMinutes: 25,
    });
    expect(result.task).toBeNull();
  });

  it("normalisiert eine abgeschlossene Aufgabe", () => {
    const result = normalizeStudyAction({
      id: 7,
      subject_name: "Mathematik",
      subject_color: "#4f46e5",
      title: "Ableitungen",
      duration_minutes: 25,
      status: "completed",
      completed_at: "2026-09-25T10:25:00.000Z",
    }, 7);

    expect(result.task).toMatchObject({
      id: "7",
      subject: "Mathematik",
      title: "Ableitungen",
      status: "completed",
      progress: 100,
    });
  });

  it("erkennt Demo-Aufgaben", () => {
    expect(isDemoTaskId("demo-task-1")).toBe(true);
    expect(isDemoTaskId(7)).toBe(false);
  });
});

describe("Dashboard-Validierung", () => {
  it("akzeptiert das Backend-Dashboard und rechnet Fortschritt um", () => {
    const result = normalizeDashboard({
      generated_at: "2026-09-25T08:00:00.000Z",
      timezone: "Europe/Berlin",
      summary: {
        completed_lessons: 1,
        total_lessons: 2,
        day_progress_percent: 50,
        open_homework: 1,
        upcoming_exams: 1,
        planned_minutes: 25,
      },
      lessons: [],
      upcoming_exams: [],
      homework: [],
      study_tasks: [],
      calendar_events: [],
    });

    expect(result.summary.lessonsCompleted).toBe(1);
    expect(result.summary.homeworkPending).toBe(1);
    expect(result.summary.dayProgress).toBe(50);
  });

  it("leitet fehlende Prüfungswerte aus Topic-Sicherheit ab", () => {
    const result = normalizeDashboard({
      upcoming_exams: [
        {
          id: 3,
          subject_name: "Mathematik",
          title: "Analysis",
          date: "2026-09-30T09:00:00.000Z",
          topics: [
            { topic_id: 1, name: "Ableitungen", confidence: 0.25 },
            { topic_id: 2, name: "Integration", confidence: 0.75 },
          ],
        },
      ],
    });

    expect(result.upcomingExams[0].progress).toBe(50);
  });

  it("lehnt fehlerhafte API-Daten ab", () => {
    expect(() => normalizeDashboard({ lessons: [{ id: 1, start_time: "kein-datum" }] })).toThrow();
  });
});
