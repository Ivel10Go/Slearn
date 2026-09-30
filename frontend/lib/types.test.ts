import { describe, expect, it } from "vitest";
import {
  parseCalendarFeed,
  parseCalendarWriting,
  parseDocumentAnswer,
  parseDocumentArray,
  parseDocumentSearchResult,
  parseIntegrationAuthorize,
  parseIntegrationCatalogArray,
  parseIntegrationSyncResult,
} from "./schemas";
import { isDemoTaskId, normalizeDashboard, normalizeStudyAction, normalizeStudyTask } from "./types";

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

  it("haelt Wichtigkeit, Frist und Warteschlange fuer die Kachel fest", () => {
    // Das sind die Angaben, an denen die Kachel "Heute lernen" haengt. Ohne
    // sie koennte sie eine ueberfaellige Aufgabe nicht von einer normalen
    // unterscheiden und wuesste nichts von einer blockierten.
    const aufgabe = normalizeStudyTask({
      id: 7,
      subject_name: "Mathematik",
      title: "Ableitungen",
      duration_minutes: 25,
      status: "pending",
      priority: 4,
      due_date: "2026-10-01T21:59:00.000Z",
      blocked_by: [{ task_id: 3, title: "Grundlagen", status: "pending", due_date: null }],
    });
    expect(aufgabe.priority).toBe(4);
    expect(aufgabe.dueDate).toBe("2026-10-01T21:59:00.000Z");
    expect(aufgabe.blockedBy).toEqual(["Grundlagen"]);
    expect(aufgabe.isSeries).toBe(false);
  });

  it("behandelt fehlende Frist als null und klemmt die Wichtigkeit", () => {
    const ohneFrist = normalizeStudyTask({ id: 7, title: "Ableitungen", status: "pending" });
    expect(ohneFrist.dueDate).toBeNull();
    expect(ohneFrist.priority).toBe(0);
    expect(ohneFrist.blockedBy).toEqual([]);

    // Eine Wichtigkeit ausserhalb 0..5 gibt das Backend nicht zurueck. Sie
    // wird hier trotzdem geklemmt, damit eine kaputte Antwort die Anzeige
    // nicht sprengt - und nicht auf einen leeren Fortschrittsbalken fuehrt.
    const zuHoch = normalizeStudyTask({ id: 8, title: "Klausur", status: "pending", priority: 99 });
    expect(zuHoch.priority).toBe(5);
    const zuNiedrig = normalizeStudyTask({ id: 9, title: "Klausur", status: "pending", priority: -3 });
    expect(zuNiedrig.priority).toBe(0);
  });

  it("erkennt eine Serienaufgabe an beiden Kennzeichen", () => {
    expect(normalizeStudyTask({ id: 1, title: "Vokabeln", status: "pending", is_series: true }).isSeries).toBe(true);
    // Manche Aeltere Antworten kennen `is_series` nicht, tragen aber eine
    // Serien-Id. Ohne den zweiten Weg bliebe die Kachel ohne Badge.
    expect(normalizeStudyTask({ id: 1, title: "Vokabeln", status: "pending", series_id: 4 }).isSeries).toBe(true);
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

describe("Dokumente-Parsing", () => {
  /**
   * Ein vollstaendiges Sync-Ergebnis. Die Kalenderfelder sind Pflicht, weil der
   * Server sie bei jedem Abruf mitschickt - auch bei einem Dokumentabruf, wo
   * sie null sind. Sie deshalb hier nicht zu streichen: das Schema soll eine
   * unvollstaendige Antwort wirklich ablehnen.
   */
  function syncErgebnis(overrides: Record<string, unknown> = {}) {
    return {
      ok: true,
      status: "success",
      message: "Sync fertig.",
      missing_fields: [],
      imported_lessons: 0,
      subjects: 0,
      teachers: 0,
      rooms: 0,
      skipped_entries: 0,
      imported_documents: 3,
      updated_documents: 1,
      indexed_chunks: 42,
      removed_documents: 0,
      skipped_files: 1,
      imported_events: 0,
      updated_events: 0,
      pushed_events: 0,
      deleted_events: 0,
      removed_events: 0,
      conflicts: 0,
      floating_events: 0,
      window_start: null,
      window_end: null,
      last_sync_at: null,
      ...overrides,
    };
  }

  it("behaelt die Dokumentfelder im Sync-Ergebnis", () => {
    const result = parseIntegrationSyncResult(syncErgebnis());

    expect(result).toMatchObject({ imported_documents: 3, indexed_chunks: 42, skipped_files: 1 });
  });

  it("behaelt die Kalenderfelder im Sync-Ergebnis", () => {
    const result = parseIntegrationSyncResult(
      syncErgebnis({ imported_events: 4, pushed_events: 1, conflicts: 2, floating_events: 3 }),
    );

    expect(result).toMatchObject({ imported_events: 4, pushed_events: 1, conflicts: 2, floating_events: 3 });
  });

  it("lehnt ein Sync-Ergebnis ohne Kalenderzahlen ab", () => {
    const unvollstaendig = syncErgebnis() as Record<string, unknown>;
    delete unvollstaendig.conflicts;

    expect(() => parseIntegrationSyncResult(unvollstaendig)).toThrow();
  });

  it("liest die Alternativgruppen und das Schreibrecht aus dem Katalog", () => {
    const katalog = parseIntegrationCatalogArray([
      {
        kind: "ical",
        label: "iCalendar (ICS)",
        description: "Liest Termine.",
        required_config: [],
        required_one_of: [["url", "path"]],
        required_credentials: [],
        optional_credentials: [],
        supports_writing: false,
      },
      {
        kind: "google_calendar",
        label: "Google Kalender",
        description: "Liest und schreibt.",
        required_config: ["client_id"],
        required_one_of: [],
        required_credentials: ["client_secret"],
        optional_credentials: ["refresh_token"],
        supports_writing: true,
      },
    ]);

    expect(katalog[0].required_one_of).toEqual([["url", "path"]]);
    expect(katalog[0].supports_writing).toBe(false);
    expect(katalog[1].supports_writing).toBe(true);
  });

  it("liest die Zustimmung zum Schreiben und ihre Verwendungen", () => {
    const zustimmung = parseCalendarWriting({
      share_calendar_for_writing: false,
      preference_key: "share_calendar_for_writing",
      uses: ["Termine schreiben"],
    });

    expect(zustimmung.share_calendar_for_writing).toBe(false);
    expect(zustimmung.uses).toEqual(["Termine schreiben"]);
    // Fehlt ein Feld, ist das eine kaputte Antwort und kein Standard.
    expect(() => parseCalendarWriting({ preference_key: "x", uses: [] })).toThrow();
  });

  it("liest die abonnierbare Adresse und den Zustand der Rotation", () => {
    const feed = parseCalendarFeed({
      url_path: "/api/v1/calendar/feed/abc",
      active: true,
      rotated: true,
      message: "Neuer Schlüssel.",
    });

    expect(feed.active).toBe(true);
    expect(feed.rotated).toBe(true);
  });

  it("liest die Autorisierungsadresse und die verlangten Rechte", () => {
    const autorisierung = parseIntegrationAuthorize({
      authorization_url: "https://accounts.google.com/o/oauth2/v2/auth?state=x",
      state: "signiert",
      scopes: ["https://www.googleapis.com/auth/calendar.events"],
      message: "Google zeigt jetzt an, welche Rechte die Verbindung braucht.",
    });

    expect(autorisierung.authorization_url).toContain("state=x");
    expect(autorisierung.scopes).toHaveLength(1);
  });

  it("normalisiert eine Dokumentliste auf string | null", () => {
    const result = parseDocumentArray([
      { id: 1, filename: "notizen.txt", source: "upload", status: "indexed", created_at: "2026-09-25T08:00:00.000Z" },
    ]);
    expect(result).toMatchObject([{ filename: "notizen.txt", external_id: null, processed_at: null }]);
  });

  it("behaelt Treffer und erlaubt fehlende Distanz", () => {
    const result = parseDocumentSearchResult({
      query: "Pythagoras",
      hits: [{ document_id: 1, filename: "mathe.txt", chunk_index: 0, content: "…" }],
    });
    expect(result.hits[0]).toMatchObject({ document_id: 1, chunk_index: 0, distance: null });
  });

  it("normalisiert unbekannte Herkunft auf fallback", () => {
    const result = parseDocumentAnswer({
      answer: "Antwort.",
      source: "unbekannt",
      model: null,
      sources: [],
    });
    expect(result).toMatchObject({ source: "fallback", model: null });
    expect(result.sources).toEqual([]);
  });
});
