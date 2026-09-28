import { parseDashboardApiResponse, parseGreetingResponse, parseScheduleRangeResponse, parseStudyActionResponse, parseStudySession, parseStudyTask } from "./schemas";

export type ApiIdentifier = string | number;
export type StudyTaskStatus = "todo" | "in_progress" | "completed";
export type StudySessionState = "active" | "stopped" | "completed";
export type LessonState = "completed" | "current" | "upcoming";

// Use any for dashboard response and related types since schemas return passthrough objects
export type DashboardApiResponse = any;
export type ApiLesson = any;
export type ApiExam = any;
export type ApiHomework = any;
export type ApiStudyTask = any;
export type ApiCalendarEvent = any;
export type ApiDashboardSummary = any;

export interface UserResponse {
  id: ApiIdentifier;
  username: string;
  email?: string | null;
  created_at?: string | null;
  is_active?: boolean;
  is_admin?: boolean;
  timezone?: string;
  last_login_at?: string | null;
}

export interface UserSessionResponse {
  user?: UserResponse | null;
  authenticated?: boolean;
}

export interface DashboardLesson {
  id: string;
  subject: string;
  subjectShort: string | null;
  title: string;
  teacher: string;
  room: string;
  startTime: string;
  endTime: string;
  color: string;
  isCancelled: boolean;
}

export interface DashboardExam {
  id: string;
  subject: string;
  title: string;
  date: string;
  description: string;
  topics: string[];
  progress: number | null;
}

export interface DashboardHomework {
  id: string;
  subject: string;
  title: string;
  description: string;
  dueDate: string;
  completed: boolean;
}

export interface StudySession {
  id: string;
  taskId: string;
  startedAt: string;
  endedAt: string | null;
  durationMinutes: number | null;
  state: StudySessionState;
}

export interface DashboardStudyTask {
  id: string;
  subject: string;
  title: string;
  topic: string;
  durationMinutes: number;
  status: StudyTaskStatus;
  progress: number;
  scheduledFor: string | null;
  startedAt: string | null;
  session: StudySession | null;
}

export interface DashboardCalendarEvent {
  id: string;
  title: string;
  description: string;
  startTime: string;
  endTime: string;
  allDay: boolean;
}

export interface DashboardSummary {
  lessonsCompleted: number;
  lessonsTotal: number;
  tasksCompleted: number;
  tasksTotal: number;
  homeworkPending: number;
  upcomingExams: number;
  dayProgress: number;
}

export interface DashboardData {
  lessons: DashboardLesson[];
  upcomingExams: DashboardExam[];
  homework: DashboardHomework[];
  studyTasks: DashboardStudyTask[];
  calendarEvents: DashboardCalendarEvent[];
  summary: DashboardSummary;
}

/** Stunden eines frei gewählten Zeitraums, wie ``GET /v1/lessons`` sie liefert. */
export interface ScheduleRange {
  rangeStart: string | null;
  rangeEnd: string | null;
  lessons: DashboardLesson[];
}

/**
 * Der Willkommenssatz des Dashboards.
 *
 * `source` unterscheidet, ob ein Modell geschrieben hat oder der Satz aus den
 * Daten gebaut wurde. Die Oberfläche zeigt das als kleinen Hinweis, damit ein
 * Satz nicht als KI-Aussage missverstanden wird.
 */
export interface Greeting {
  text: string;
  source: "ai" | "fallback";
  model: string | null;
  generatedOn: string;
}

export interface StudyActionResult {
  task: DashboardStudyTask | null;
  session: StudySession | null;
}

export interface IntegrationRead {
  id: number;
  kind: string;
  name: string;
  enabled: boolean;
  status: string;
  config: Record<string, unknown>;
  has_credentials: boolean;
  credential_keys: string[];
  missing_fields: string[];
  last_error?: string | null;
  last_tested_at?: string | null;
  last_sync_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface IntegrationCatalog {
  kind: string;
  label: string;
  description: string;
  required_config: string[];
  required_credentials: string[];
  optional_credentials: string[];
}

export interface IntegrationTestResult {
  ok: boolean;
  status: string;
  message: string;
  missing_fields: string[];
}

export interface IntegrationSyncResult extends IntegrationTestResult {
  imported_lessons: number;
  subjects: number;
  teachers: number;
  rooms: number;
  skipped_entries: number;
  imported_documents: number;
  updated_documents: number;
  indexed_chunks: number;
  removed_documents: number;
  skipped_files: number;
  window_start: string | null;
  window_end: string | null;
  last_sync_at: string | null;
}

export interface DocumentRecord {
  id: number;
  filename: string;
  source: string;
  status: string;
  external_id: string | null;
  mime_type: string | null;
  created_at: string;
  processed_at: string | null;
}

export interface DocumentSource {
  document_id: number;
  filename: string;
  chunk_index: number;
  content: string;
}

export interface DocumentHit extends DocumentSource {
  distance: number | null;
}

export interface DocumentSearchResult {
  query: string;
  hits: DocumentHit[];
}

export type DocumentAnswerSource = "ai" | "fallback" | "none";

export interface DocumentAnswer {
  answer: string;
  source: DocumentAnswerSource;
  model: string | null;
  sources: DocumentSource[];
}

// Helper functions
function text(value: unknown, fallback: string) {
  const normalized = typeof value === "string" ? value.trim() : "";
  return normalized ? normalized : fallback;
}

function numberValue(value: number | null | undefined, fallback: number) {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function clampProgress(value: number | null | undefined, fallback = 0) {
  const numeric = numberValue(value, fallback);
  const percentage = numeric > 0 && numeric <= 1 ? numeric * 100 : numeric;
  return Math.min(100, Math.max(0, Math.round(percentage)));
}

function normalizeStatus(value: string | null | undefined, progress: number, session: StudySession | null): StudyTaskStatus {
  const status = (value ?? "").toString().trim().toLowerCase().replaceAll("-", "_").replaceAll(" ", "_");
  if (session?.state === "completed" || status === "completed" || status === "done" || status === "finished" || status === "cancelled" || progress >= 100) {
    return "completed";
  }
  if (session?.state === "active" || status === "in_progress" || status === "active" || status === "started" || status === "running") {
    return "in_progress";
  }
  return "todo";
}

function normalizeTopics(value: Array<string | { name?: string }> | null | undefined) {
  if (!Array.isArray(value)) return [];
  return value
    .map((topic) => (typeof topic === "string" ? text(topic, "Thema") : text(topic.name, "Thema")))
    .filter(Boolean);
}

function examProgress(progress: number | null | undefined, topics: Array<string | { confidence?: number }> | null | undefined) {
  if (typeof progress === "number" && Number.isFinite(progress)) {
    return clampProgress(progress);
  }
  const confidences = Array.isArray(topics)
    ? topics
        .map((topic) => (typeof topic === "object" && topic !== null && typeof topic.confidence === "number" && Number.isFinite(topic.confidence) ? topic.confidence : null))
        .filter((c): c is number => c !== null)
    : [];
  return confidences.length > 0 ? clampProgress(confidences.reduce((s, c) => s + c, 0) / confidences.length) : null;
}

export function normalizeStudySession(payload: unknown, fallbackTaskId: ApiIdentifier): StudySession | null {
  const parsed: any = parseStudySession(payload);
  const startedAt = parsed.started_at ?? parsed.start ?? parsed.start_time;
  if (!startedAt) return null;
  const endedAt = parsed.completed_at ?? parsed.ended_at ?? parsed.end_time ?? null;
  const status = (parsed.status ?? "").toString().trim().toLowerCase();
  const state: StudySessionState = status === "completed" || (status !== "stopped" && Boolean(parsed.completed_at))
    ? "completed"
    : status === "stopped" || Boolean(parsed.ended_at ?? parsed.end_time)
    ? "stopped"
    : "active";
  const startTime = new Date(String(startedAt)).getTime();
  const endTime = endedAt ? new Date(String(endedAt)).getTime() : null;
  const calculatedDuration = endTime !== null && Number.isFinite(startTime) && Number.isFinite(endTime)
    ? Math.max(0, Math.round((endTime - startTime) / 60000))
    : null;
  const durationMinutes = parsed.duration_minutes ?? calculatedDuration;
  return {
    id: String(parsed.id ?? parsed.task_id ?? fallbackTaskId),
    taskId: String(parsed.task_id ?? fallbackTaskId),
    startedAt: String(startedAt),
    endedAt: endedAt ? String(endedAt) : null,
    durationMinutes: durationMinutes === null ? null : Math.max(0, Math.round(durationMinutes)),
    state,
  };
}

export function normalizeStudyTask(task: unknown): DashboardStudyTask {
  const parsed: any = parseStudyTask(task);
  const rawSession = parsed.session ?? parsed.study_session ?? parsed.active_session;
  const session = rawSession ? normalizeStudySession(rawSession, parsed.id) : parsed.started_at ?? parsed.session_started_at
    ? normalizeStudySession({ started_at: parsed.started_at ?? parsed.session_started_at, ended_at: parsed.completed_at }, parsed.id)
    : null;
  const initialStatus = normalizeStatus(parsed.status, parsed.progress ?? 0, session);
  const progress = clampProgress(parsed.progress, initialStatus === "completed" ? 100 : 0);
  const status = normalizeStatus(parsed.status, progress, session);
  return {
    id: String(parsed.id),
    subject: text(parsed.subject ?? parsed.subject_name, "Lernen"),
    title: text(parsed.title, "Lernaufgabe"),
    topic: text(parsed.topic ?? parsed.topic_name, "Selbstständiges Lernen"),
    durationMinutes: Math.max(0, Math.round(numberValue(parsed.duration_minutes, 25))),
    status,
    progress,
    scheduledFor: parsed.scheduled_for ?? null,
    startedAt: session?.startedAt ?? parsed.started_at ?? parsed.session_started_at ?? null,
    session,
  };
}

export function normalizeStudyAction(payload: unknown, fallbackTaskId: ApiIdentifier): StudyActionResult {
  const parsed: any = parseStudyActionResponse(payload);
  const source = typeof parsed === "object" && parsed !== null && "data" in parsed ? parsed.data : parsed;
  if (typeof source !== "object" || source === null) throw new Error("Die Lernaktionsantwort ist ungültig.");
  const taskPayload = "task" in source ? source.task : "title" in source ? source : null;
  const task = typeof taskPayload === "object" && taskPayload !== null ? normalizeStudyTask(taskPayload) : null;
  const sessionPayload = "session" in source ? source.session : "study_session" in source ? source.study_session : "active_session" in source ? source.active_session : source.started_at || source.start || source.start_time ? source : task?.session;
  const session = sessionPayload ? normalizeStudySession(sessionPayload, task?.id ?? fallbackTaskId) : task?.session ?? null;
  return { task, session };
}

export function isDemoTaskId(taskId: ApiIdentifier) {
  return String(taskId).toLowerCase().startsWith("demo-");
}

function record(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null ? (value as Record<string, unknown>) : {};
}

function normalizeLesson(raw: unknown): DashboardLesson {
  const lesson = record(raw);
  // Die API liefert ISO-Strings; ein fehlender Zeitpunkt darf den Render nicht sprengen.
  const startTime = text(lesson.start_time, "");
  const subjectName = text(lesson.subject ?? lesson.subject_name, "Unbekanntes Fach");
  return {
    id: String(lesson.id ?? ""),
    subject: subjectName,
    // Ohne Kuerzel aus dem Portal bleibt der ausgeschriebene Name stehen,
    // damit die Kachel nie leer wird.
    subjectShort: text(lesson.subject_short_name, subjectName),
    title: text(lesson.title ?? lesson.subject_name ?? lesson.subject, "Unterricht"),
    teacher: text(lesson.teacher ?? lesson.teacher_name, "Nicht angegeben"),
    room: text(lesson.room ?? lesson.room_name, "Nicht angegeben"),
    startTime,
    endTime: text(lesson.end_time, startTime),
    color: text(lesson.color ?? lesson.subject_color, "#6366f1"),
    isCancelled: lesson.is_cancelled === true,
  };
}

/** Die API antwortet mit UTC-Zeitpunkten; die Gruppierung nach Tag übernimmt der Client. */
export function normalizeScheduleRange(payload: unknown): ScheduleRange {
  const parsed = record(parseScheduleRangeResponse(payload));
  return {
    rangeStart: typeof parsed.range_start === "string" ? parsed.range_start : null,
    rangeEnd: typeof parsed.range_end === "string" ? parsed.range_end : null,
    lessons: Array.isArray(parsed.lessons) ? parsed.lessons.map((lesson) => normalizeLesson(lesson)) : [],
  };
}

export function normalizeGreeting(payload: unknown): Greeting {
  const parsed = record(parseGreetingResponse(payload));
  return {
    text: text(parsed.text, "Herzlich willkommen."),
    // Das Backend kennt nur "ai" und "fallback"; alles andere wird als Fallback
    // behandelt, damit die Oberflaeche nie eine falsche Herkunft behauptet.
    source: parsed.source === "ai" ? "ai" : "fallback",
    model: typeof parsed.model === "string" && parsed.model ? parsed.model : null,
    generatedOn: text(parsed.generated_on, ""),
  };
}

export function normalizeDashboard(payload: unknown): DashboardData {
  const parsed: any = parseDashboardApiResponse(payload);
  const lessons = (parsed.lessons ?? []).map((lesson: unknown) => normalizeLesson(lesson));
  const upcomingExams = (parsed.upcoming_exams ?? []).map((exam: any) => ({
    id: String(exam.id),
    subject: text(exam.subject ?? exam.subject_name, "Allgemein"),
    title: text(exam.title, "Prüfung"),
    date: exam.date,
    description: text(exam.description, "Vorbereitung auf die nächste Prüfung."),
    topics: normalizeTopics(exam.topics),
    progress: examProgress(exam.progress, exam.topics),
  }));
  const homework = (parsed.homework ?? []).map((item: any) => ({
    id: String(item.id),
    subject: text(item.subject ?? item.subject_name, "Allgemein"),
    title: text(item.title, "Hausaufgabe"),
    description: text(item.description, ""),
    dueDate: item.due_date,
    completed: item.completed,
  }));
  const studyTasks = (parsed.study_tasks ?? []).map((task: any) => normalizeStudyTask(task));
  const calendarEvents = (parsed.calendar_events ?? []).map((event: any) => ({
    id: String(event.id),
    title: text(event.title, "Kalendereintrag"),
    description: text(event.description, ""),
    startTime: event.start_time,
    endTime: text(event.end_time, event.start_time),
    allDay: event.all_day === true,
  }));
  const sourceSummary = parsed.summary ?? {};
  const lessonsCompleted = numberValue(sourceSummary.lessons_completed ?? sourceSummary.completed_lessons, lessons.filter((l: DashboardLesson) => new Date(l.endTime).getTime() <= Date.now()).length);
  const lessonsTotal = numberValue(sourceSummary.lessons_total ?? sourceSummary.total_lessons, lessons.length);
  const tasksCompleted = numberValue(sourceSummary.tasks_completed ?? sourceSummary.completed_tasks, studyTasks.filter((t: DashboardStudyTask) => t.status === "completed").length);
  const tasksTotal = numberValue(sourceSummary.tasks_total ?? sourceSummary.total_tasks, studyTasks.length);
  const homeworkPending = numberValue(sourceSummary.homework_pending ?? sourceSummary.pending_homework ?? sourceSummary.open_homework, homework.filter((i: DashboardHomework) => !i.completed).length);
  const upcomingExamCount = numberValue(sourceSummary.upcoming_exams ?? sourceSummary.exams_upcoming, upcomingExams.length);
  const dayProgress = clampProgress(sourceSummary.day_progress ?? sourceSummary.day_progress_percent ?? sourceSummary.progress);
  return {
    lessons,
    upcomingExams,
    homework,
    studyTasks,
    calendarEvents,
    summary: {
      lessonsCompleted,
      lessonsTotal,
      tasksCompleted,
      tasksTotal,
      homeworkPending,
      upcomingExams: upcomingExamCount,
      dayProgress,
    },
  };
}