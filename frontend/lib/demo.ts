import { addDays, toDate, toIsoDate } from "./format";
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

interface DemoSlot {
  subject: string;
  subjectShort: string;
  title: string;
  teacher: string;
  room: string;
  color: string;
  hour: number;
  minute: number;
  cancelled?: boolean;
}

const DEMO_LESSON_MINUTES = 50;

/** Ein Schulwochenplan, indexiert ab Montag (0). Das Wochenende bleibt leer. */
const DEMO_WEEK: DemoSlot[][] = [
  [
    { subject: "Mathematik", subjectShort: "M", title: "Analysis", teacher: "Frau Berger", room: "204", color: "#6366f1", hour: 8, minute: 0 },
    { subject: "Deutsch", subjectShort: "D", title: "Essay", teacher: "Herr Adler", room: "111", color: "#0ea5e9", hour: 10, minute: 15 },
    { subject: "Sport", subjectShort: "SJ", title: "Hallentraining", teacher: "Frau Peters", room: "Sporthalle", color: "#f59e0b", hour: 14, minute: 0 },
  ],
  [
    { subject: "Englisch", subjectShort: "E", title: "Presentations", teacher: "Mr. Wilson", room: "112", color: "#0ea5e9", hour: 9, minute: 35 },
    { subject: "Physik", subjectShort: "Ph", title: "Mechanik", teacher: "Dr. Roth", room: "Labor 1", color: "#22c55e", hour: 11, minute: 0 },
    { subject: "Mathematik", subjectShort: "M", title: "Lineare Algebra", teacher: "Frau Berger", room: "204", color: "#6366f1", hour: 13, minute: 15 },
  ],
  [
    { subject: "Chemie", subjectShort: "Ch", title: "Organische Chemie", teacher: "Herr Klein", room: "Labor 2", color: "#f59e0b", hour: 8, minute: 0 },
    { subject: "Geschichte", subjectShort: "G", title: "Neuzeit", teacher: "Frau Schmitt", room: "105", color: "#a855f7", hour: 10, minute: 0 },
    { subject: "Französisch", subjectShort: "F", title: "Grammatik", teacher: "Frau Neumann", room: "201", color: "#ec4899", hour: 12, minute: 30 },
    { subject: "Latein", subjectShort: "L", title: "Cicero", teacher: "Herr Krause", room: "203", color: "#14b8a6", hour: 12, minute: 30 },
    { subject: "Englisch", subjectShort: "E", title: "Listening", teacher: "Mr. Wilson", room: "112", color: "#0ea5e9", hour: 14, minute: 0, cancelled: true },
  ],
  [
    { subject: "Physik", subjectShort: "Ph", title: "Elektrizität", teacher: "Dr. Roth", room: "Labor 1", color: "#22c55e", hour: 9, minute: 0 },
    { subject: "Deutsch", subjectShort: "D", title: "Textanalyse", teacher: "Herr Adler", room: "111", color: "#0ea5e9", hour: 11, minute: 15 },
    { subject: "Sport", subjectShort: "SW", title: "Ausdauer", teacher: "Frau Peters", room: "Sporthalle", color: "#f59e0b", hour: 14, minute: 45 },
  ],
  [
    { subject: "Chemie", subjectShort: "Ch", title: "Reaktionen", teacher: "Herr Klein", room: "Labor 2", color: "#f59e0b", hour: 8, minute: 30 },
    { subject: "Mathematik", subjectShort: "M", title: "Analysis", teacher: "Frau Berger", room: "204", color: "#6366f1", hour: 10, minute: 0 },
    { subject: "Ethik", subjectShort: "eth", title: "Argumentation", teacher: "Frau Yilmaz", room: "323", color: "#84cc16", hour: 11, minute: 30 },
    { subject: "ev. Religion", subjectShort: "eR", title: "Ethische Fragen", teacher: "Frau Brandt", room: "102", color: "#a3a3a3", hour: 11, minute: 30 },
    { subject: "kath. Religion", subjectShort: "kR", title: "Der Glaube", teacher: "Herr Simon", room: "110", color: "#a3a3a3", hour: 11, minute: 30 },
  ],
];

function slotAt(day: Date, slot: DemoSlot) {
  const start = new Date(day);
  start.setHours(slot.hour, slot.minute, 0, 0);
  const end = new Date(start.getTime());
  end.setMinutes(end.getMinutes() + DEMO_LESSON_MINUTES);
  return { start, end };
}

function startOfDemoDay(value: Date) {
  const day = new Date(value.getTime());
  day.setHours(0, 0, 0, 0);
  return day;
}

/** Baut einen beliebigen Zeitraum, damit der Wochenplan auch offline etwas zeigt. */
export function getDemoScheduleRange(startIso: string, endIso: string): DashboardApiResponse {
  const start = toDate(startIso);
  const end = toDate(endIso);
  if (!start || !end || end.getTime() < start.getTime()) {
    return { range_start: null, range_end: null, lessons: [] };
  }

  const lessons: Record<string, unknown>[] = [];
  for (let day = startOfDemoDay(start); day.getTime() <= end.getTime(); day = addDays(day, 1)) {
    const weekday = (day.getDay() + 6) % 7;
    const slots = weekday > 4 ? [] : DEMO_WEEK[weekday];
    slots.forEach((slot, index) => {
      const { start: lessonStart, end: lessonEnd } = slotAt(day, slot);
      lessons.push({
        id: `demo-lesson-${toIsoDate(day)}-${index}`,
        subject: slot.subject,
        subject_short_name: slot.subjectShort,
        title: slot.title,
        teacher: slot.teacher,
        room: slot.room,
        color: slot.color,
        is_cancelled: slot.cancelled === true,
        start_time: lessonStart.toISOString(),
        end_time: lessonEnd.toISOString(),
      });
    });
  }

  return { range_start: startIso, range_end: endIso, lessons };
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
