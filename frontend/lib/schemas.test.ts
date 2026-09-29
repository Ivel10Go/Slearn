import { describe, expect, it } from "vitest";
import {
  collectSubjectNames,
  parseCalendarEventArray,
  parseExamArray,
  parseExamTaskPlanResult,
  parseFileUploadResponse,
  parseHomeworkArray,
  parseStudyPlanRead,
  parseStudySeriesRead,
  parseStudySessionRead,
  parseStudyTaskArray,
  parseSubjectArray,
} from "./schemas";

describe("Fächer", () => {
  it("macht aus fehlenden Angaben ausdrücklich null", () => {
    const [subject] = parseSubjectArray([{ id: 1, name: "Mathe", color: "#3B82F6", source: "manual", created_at: "2026-03-04T07:45:00Z" }]);
    expect(subject.teacher_id).toBeNull();
    expect(subject.description).toBeNull();
    expect(subject.external_id).toBeNull();
  });

  it("weist eine Antwort ohne Namen zurück", () => {
    expect(() => parseSubjectArray([{ id: 1, color: "#fff" }])).toThrow();
  });
});

describe("Hausaufgaben", () => {
  it("liest Erledigt-Zeitpunkt und Fachbezeichnung", () => {
    const [item] = parseHomeworkArray([
      {
        id: 4,
        title: "Text lesen",
        due_date: "2026-03-04T16:00:00Z",
        completed: true,
        completed_at: "2026-03-04T12:00:00Z",
        source: "webuntis",
        subject_name: "Deutsch",
        subject_color: "#111111",
      },
    ]);
    expect(item.completed).toBe(true);
    expect(item.completed_at).toBe("2026-03-04T12:00:00Z");
    expect(item.subject_name).toBe("Deutsch");
    expect(item.subject_id).toBeNull();
  });

  it("weist eine Aufgabe ohne Faelligkeit zurück", () => {
    expect(() => parseHomeworkArray([{ id: 4, title: "Text lesen", completed: false, source: "manual" }])).toThrow();
  });
});

describe("Prüfungen", () => {
  it("behaelt days_until auch bei null", () => {
    const [exam] = parseExamArray([{ id: 2, title: "Klassenarbeit", date: "2026-03-10T08:00:00Z", source: "manual" }]);
    expect(exam.days_until).toBeNull();
    expect(exam.date).toBe("2026-03-10T08:00:00Z");
  });
});

describe("Kalender", () => {
  it("liest all_day und behandelt fehlende Beschreibung als null", () => {
    const [event] = parseCalendarEventArray([
      { id: 3, title: "Sportfest", start_time: "2026-03-12T08:00:00Z", end_time: "2026-03-12T16:00:00Z", all_day: true, source: "manual" },
    ]);
    expect(event.all_day).toBe(true);
    expect(event.description).toBeNull();
  });

  it("weist ein Ereignis ohne Ende zurück", () => {
    expect(() => parseCalendarEventArray([{ id: 3, title: "Sportfest", start_time: "2026-03-12T08:00:00Z", all_day: true, source: "manual" }])).toThrow();
  });
});

describe("Lernaufgaben", () => {
  // `priority`, `blocked_by` und `blocking_task_ids` sind im Backend immer
  // gesetzt und im Schema bewusst **nicht** optional. Der Test gibt sie mit -
  // fehlten sie, wuerde die Antwort abgewiesen, und eine echte kaputte
  // Antwort ist dann von einer alten zu unterscheiden.
  const aufgabenBausatz = { id: 1, study_plan_id: 9, title: "Ableitungen", duration_minutes: 25, order_index: 0, priority: 0, blocked_by: [], blocking_task_ids: [] };

  it("akzeptiert genau die vier Zustände des Backends", () => {
    for (const status of ["pending", "in_progress", "completed", "cancelled"]) {
      expect(parseStudyTaskArray([{ ...aufgabenBausatz, status }])[0].status).toBe(status);
    }
  });

  it("weist einen unbekannten Zustand zurück, statt ihn zu raten", () => {
    // `todo` steht in keinem Backend-Schema. Ein stilles Umdeuten wuerde eine
    // abgelehnte Aufgabe als "offen" anzeigen und den Fehler verstecken.
    expect(() => parseStudyTaskArray([{ ...aufgabenBausatz, status: "todo" }])).toThrow();
  });

  it("weist eine Antwort ohne Prioritaet zurueck", () => {
    // `delete` statt Rest-Operator mit Wegwerfvariable: so bleibt fuer den
    // Linter sichtbar, dass `priority` wirklich fehlt und nicht nur leer ist.
    const ohnePrioritaet: Record<string, unknown> = { ...aufgabenBausatz, status: "pending" };
    delete ohnePrioritaet.priority;
    expect(() => parseStudyTaskArray([ohnePrioritaet])).toThrow();
  });

  it("liest die Session von `POST .../start`", () => {
    const session = parseStudySessionRead({ id: 5, task_id: 1, started_at: "2026-03-04T07:45:00Z" });
    expect(session.task_id).toBe(1);
    expect(session.completed_at).toBeNull();
    expect(session.duration_minutes).toBeNull();
  });

  it("behaelt die offenen Voraussetzungen und den Klausurnamen", () => {
    // Das sind die beiden Angaben, an denen der Task-Manager haengt: ohne
    // sie wuesste die Zeile nicht, dass sie blockiert ist, und nicht, zu
    // welcher Klausur sie gehoert.
    const aufgabe = parseStudyTaskArray([
      {
        ...aufgabenBausatz,
        status: "pending",
        exam_id: 4,
        exam_title: "Klausur Analysis",
        series_id: 2,
        series_recurrence: "weekly",
        blocked_by: [{ task_id: 7, title: "Grundlagen", status: "pending", due_date: null }],
      },
    ])[0];
    expect(aufgabe.exam_title).toBe("Klausur Analysis");
    expect(aufgabe.series_recurrence).toBe("weekly");
    expect(aufgabe.blocked_by.map((warte) => warte.title)).toEqual(["Grundlagen"]);
  });

  it("weist einen unbekannten Serientyp zurueck", () => {
    expect(() =>
      parseStudyTaskArray([{ ...aufgabenBausatz, status: "pending", series_id: 2, series_recurrence: "yearly" }]),
    ).toThrow();
  });
});

describe("Lernplaene und Serien", () => {
  it("liest die Aufgabenzahlen eines Plans", () => {
    const plan = parseStudyPlanRead({
      id: 9,
      title: "Klausur Analysis",
      status: "active",
      task_count: 12,
      open_task_count: 5,
      created_at: "2026-03-01T10:00:00Z",
      updated_at: "2026-03-02T10:00:00Z",
    });
    expect(plan.open_task_count).toBe(5);
    // Fehlende Beschreibung ist `null`, nicht `undefined` - sonst muesste die
    // Oberflaeche an jeder Stelle selbst auf beides pruefen.
    expect(plan.description).toBeNull();
    expect(plan.starts_on).toBeNull();
  });

  it("liest eine Serie und behandelt eine fehlende Obergrenze als null", () => {
    const serie = parseStudySeriesRead({
      id: 3,
      study_plan_id: 9,
      title: "Vokabeln",
      recurrence: "weekly",
      duration_minutes: 15,
      priority: 2,
      starts_on: "2026-10-05T18:00:00Z",
      until: "2026-10-12T18:00:00Z",
      next_due_on: "2026-10-12T18:00:00Z",
      occurrences: 1,
      status: "active",
      created_at: "2026-10-01T10:00:00Z",
      updated_at: "2026-10-01T10:00:00Z",
    });
    expect(serie.recurrence).toBe("weekly");
    expect(serie.occurrence_limit).toBeNull();
    expect(serie.next_due_on).toBe("2026-10-12T18:00:00Z");
  });

  it("weist einen unbekannten Serientyp zurueck", () => {
    expect(() =>
      parseStudySeriesRead({
        id: 3,
        study_plan_id: 9,
        title: "Vokabeln",
        recurrence: "yearly",
        duration_minutes: 15,
        priority: 0,
        starts_on: "2026-10-05T18:00:00Z",
        until: null,
        next_due_on: "2026-10-05T18:00:00Z",
        occurrences: 1,
        status: "active",
        created_at: "2026-10-01T10:00:00Z",
        updated_at: "2026-10-01T10:00:00Z",
      }),
    ).toThrow();
  });
});

describe("Aufgaben aus Klausurthemen", () => {
  it("liest Ergebnis und Zaehler", () => {
    const ergebnis = parseExamTaskPlanResult({
      exam_id: 4,
      study_plan_id: 9,
      created_tasks: [{ id: 1, study_plan_id: 9, title: "Ableitungen lernen", duration_minutes: 30, order_index: 0, priority: 4, status: "pending", blocked_by: [], blocking_task_ids: [] }],
      created_count: 1,
      skipped_count: 2,
      first_due_on: "2026-10-01T16:00:00Z",
      last_due_on: "2026-10-01T23:59:00Z",
    });
    // `skipped_count` ist das, was einen zweiten Klick erklaert: Themen, fuer
    // die im selben Plan schon eine offene Aufgabe haengt.
    expect(ergebnis.skipped_count).toBe(2);
    expect(ergebnis.created_tasks[0].priority).toBe(4);
  });

  it("behandelt fehlende Datumsgrenzen als null", () => {
    const ergebnis = parseExamTaskPlanResult({
      exam_id: 4,
      study_plan_id: 9,
      created_tasks: [],
      created_count: 0,
      skipped_count: 0,
    });
    expect(ergebnis.first_due_on).toBeNull();
    expect(ergebnis.last_due_on).toBeNull();
  });
});

describe("Dateiupload", () => {
  it("behaelt den Pfad, den das Backend zurueckgibt", () => {
    const result = parseFileUploadResponse({ id: 8, filename: "skript.md", path: "/data/school/8f2.md" });
    expect(result.path).toBe("/data/school/8f2.md");
    expect(result.checksum).toBeNull();
  });
});

describe("Fach-Auswahl", () => {
  it("nimmt nur gueltige Eintraege und sortiert nach Name", () => {
    const options = collectSubjectNames([
      { id: 3, name: "Zoologie", color: "#111", source: "manual", created_at: "2026-01-01T00:00:00Z" },
      { id: 1, name: "Astronomie", color: "#111", source: "manual", created_at: "2026-01-01T00:00:00Z" },
      { id: 2, name: "Physik", color: "#111", source: "manual", created_at: "2026-01-01T00:00:00Z" },
    ]);
    expect(options.map((option) => option.name)).toEqual(["Astronomie", "Physik", "Zoologie"]);
  });

  it("dedupliziert nach Id, wenn eine Liste doppelt gemischt wird", () => {
    const fach = { id: 1, name: "Mathe", color: "#111", source: "manual", created_at: "2026-01-01T00:00:00Z" };
    expect(collectSubjectNames([fach, fach])).toHaveLength(1);
  });

  it("ist tolerant gegen kaputte Eintraege", () => {
    // Eine kaputte Zeile darf den Picker nicht leeren, sonst faellt im
    // Formular jedes Fach weg.
    expect(collectSubjectNames([{ id: 1 }, null, "nichts"])).toEqual([]);
    expect(collectSubjectNames(null)).toEqual([]);
  });
});
