import type { DashboardApiResponse } from "./types";

export function isDemoFallbackEnabled() {
  return process.env.NEXT_PUBLIC_ENABLE_DEMO_FALLBACK === "true";
}

function dateAt(dayOffset: number, hour: number, minute = 0) {
  const date = new Date();
  date.setDate(date.getDate() + dayOffset);
  date.setHours(hour, minute, 0, 0);
  return date.toISOString();
}

export function getDemoDashboard(): DashboardApiResponse {
  return {
    lessons: [
      {
        id: "demo-lesson-1",
        subject: "Mathematik",
        title: "Analysis",
        teacher: "Frau Berger",
        room: "Raum 204",
        start_time: dateAt(0, 8),
        end_time: dateAt(0, 8, 50),
        color: "#6366f1",
      },
      {
        id: "demo-lesson-2",
        subject: "Englisch",
        title: "Presentations",
        teacher: "Mr. Wilson",
        room: "Raum 112",
        start_time: dateAt(0, 10),
        end_time: dateAt(0, 10, 50),
        color: "#0ea5e9",
      },
      {
        id: "demo-lesson-3",
        subject: "Chemie",
        title: "Organische Chemie",
        teacher: "Herr Klein",
        room: "Labor 2",
        start_time: dateAt(0, 13),
        end_time: dateAt(0, 13, 50),
        color: "#f59e0b",
      },
    ],
    upcoming_exams: [
      {
        id: "demo-exam-1",
        subject: "Mathematik",
        title: "Analysis I",
        date: dateAt(5, 9),
        topics: ["Ableitungen", "Integralrechnung", "Funktionsanalyse"],
        progress: 64,
      },
      {
        id: "demo-exam-2",
        subject: "Englisch",
        title: "Oral examination",
        date: dateAt(12, 11),
        topics: ["Listening", "Speaking", "Vocabulary"],
        progress: 28,
      },
      {
        id: "demo-exam-3",
        subject: "Chemie",
        title: "Reaktionsgleichungen",
        date: dateAt(19, 10),
        topics: ["Stöchiometrie", "Säuren und Basen"],
        progress: 12,
      },
    ],
    homework: [
      {
        id: "demo-homework-1",
        subject: "Mathematik",
        title: "Aufgaben 12–18 bearbeiten",
        description: "Kurze Wiederholung der Kettenregel.",
        due_date: dateAt(0, 18),
        completed: false,
      },
      {
        id: "demo-homework-2",
        subject: "Englisch",
        title: "Vokabeln Lernkarte 4",
        description: "Zehn Vokabeln wiederholen.",
        due_date: dateAt(1, 18),
        completed: false,
      },
      {
        id: "demo-homework-3",
        subject: "Chemie",
        title: "Reaktionsschema notieren",
        description: "Die wichtigsten Reaktionen aus dem Unterricht sammeln.",
        due_date: dateAt(2, 18),
        completed: false,
      },
    ],
    study_tasks: [
      {
        id: "demo-task-1",
        subject: "Mathematik",
        title: "Ableitungen üben",
        topic: "Analysis",
        duration_minutes: 25,
        status: "in_progress",
        progress: 40,
      },
      {
        id: "demo-task-2",
        subject: "Englisch",
        title: "Vokabeltest wiederholen",
        topic: "Presentation skills",
        duration_minutes: 20,
        status: "todo",
        progress: 0,
      },
      {
        id: "demo-task-3",
        subject: "Chemie",
        title: "Reaktionsgleichungen sortieren",
        topic: "Organische Chemie",
        duration_minutes: 30,
        status: "todo",
        progress: 0,
      },
    ],
    calendar_events: [
      {
        id: "demo-event-1",
        title: "Lerngruppe",
        description: "Gemeinsam Analysis wiederholen",
        start_time: dateAt(0, 16),
        end_time: dateAt(0, 17),
      },
      {
        id: "demo-event-2",
        title: "Sporttraining",
        description: "Hallentraining",
        start_time: dateAt(1, 17, 30),
        end_time: dateAt(1, 19),
      },
      {
        id: "demo-event-3",
        title: "Bibliothek",
        description: "Platz für konzentriertes Lernen",
        start_time: dateAt(3, 15),
        end_time: dateAt(3, 17),
      },
    ],
    summary: {
      lessons_completed: 1,
      lessons_total: 3,
      completed_tasks: 2,
      tasks_total: 5,
      homework_pending: 3,
      upcoming_exams: 3,
      day_progress: 42,
    },
  };
}
