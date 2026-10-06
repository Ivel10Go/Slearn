import { parseDashboardApiResponse, parseGreetingResponse, parseScheduleRangeResponse, parseStudyActionResponse, parseStudySession, parseStudyTask } from "./schemas";
import { normalizeSource, type AiSource, type AnswerSource } from "./ai-runtime";

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
  /** Faelligkeit der Aufgabe. Der Lernzeitpunkt ist davon unabhaengig. */
  dueDate: string | null;
  /** 0 bis 5. Hoeher heisst wichtiger. */
  priority: number;
  /** Titel der offenen Vorbedingungen. Nicht leer heisst: blockiert. */
  blockedBy: string[];
  /** Aus einer wiederkehrenden Serie erzeugt. */
  isSeries: boolean;
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
 * `source` sagt, **welcher Weg** geschrieben hat: `local` für einen Server auf
 * diesem Rechner, `provider` für einen Online-Dienst und `fallback` für den Satz
 * aus den Daten. Die Oberfläche zeigt das als kleinen Hinweis, damit ein Satz
 * nicht als KI-Aussage missverstanden wird.
 */
export interface Greeting {
  text: string;
  source: AiSource;
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
  /**
   * Felder, die man kann, aber nicht muss - beim KI-Server `runtime`.
   *
   * Ein Eintrag von heute hat das nicht; das ist kein Fehler. Deshalb auch
   * hier ein Default, nicht ein Pflichtfeld.
   */
  optional_config?: string[];
  /** Gruppen, von denen mindestens ein Feld kommen muss, etwa `[["url", "path"]]`. */
  required_one_of: string[][];
  required_credentials: string[];
  optional_credentials: string[];
  /** Kann in dieser Quelle geschrieben werden? */
  supports_writing: boolean;
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
  /** Neu aus einer Kalenderquelle übernommene Termine. */
  imported_events: number;
  updated_events: number;
  /** In der Quelle geschriebene Termine. Ohne Freigabe immer null. */
  pushed_events: number;
  deleted_events: number;
  removed_events: number;
  /** Termine, die an beiden Seiten geändert wurden und offen geblieben sind. */
  conflicts: number;
  floating_events: number;
  window_start: string | null;
  window_end: string | null;
  last_sync_at: string | null;
}

/** Die Adresse, auf der der Browser für die Einwilligung von Google abbiegt. */
export interface IntegrationAuthorize {
  authorization_url: string;
  state: string;
  scopes: string[];
  message: string;
}

/**
 * Die ausdrückliche Zustimmung, Termine in einen Kalender zu schreiben.
 *
 * `preference_key` nennt den Ort, an dem die Entscheidung gespeichert ist. Das
 * steht in der Antwort, damit die Oberfläche nicht einen zweiten Namen neben
 * dem echten führt.
 */
export interface CalendarWriting {
  share_calendar_for_writing: boolean;
  preference_key: string;
  /** Wozu die Freigabe benutzt wird - als Liste, nicht als Behauptung. */
  uses: string[];
}

/**
 * Die abonnierbare ICS-Adresse des Lernraums.
 *
 * `rotated` ist nach dem Erzeugen eines neuen Schlüssels wahr und weist darauf
 * hin, dass die alte Adresse nicht mehr funktioniert.
 */
export interface CalendarFeed {
  url_path: string;
  active: boolean;
  rotated: boolean;
  message: string;
}

export type MaterialKind = "aufschrieb" | "arbeitsblatt" | "loesung" | "sonstiges";

export interface DocumentRecord {
  id: number;
  filename: string;
  source: string;
  status: string;
  external_id: string | null;
  mime_type: string | null;
  created_at: string;
  processed_at: string | null;
  /** Beim Einlesen erkannt, von Hand korrigierbar. */
  material_kind: MaterialKind | null;
  material_topic: string | null;
  material_date: string | null;
  subject_id: number | null;
  /** text | pdf | ocr_local | ocr_online */
  text_source: string | null;
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

/**
 * `none` heisst "kein Treffer, also gar keine Frage" - der einzige Wert, den
 * der Willkommenssatz nicht kennt.
 */
export type DocumentAnswerSource = AnswerSource;

export interface DocumentAnswer {
  answer: string;
  source: DocumentAnswerSource;
  model: string | null;
  sources: DocumentSource[];
  /**
   * Ob der Notenstand im Prompt stand.
   *
   * Ein eigenes Feld und **kein** Wert von `source`: `source` sagt, woher die
   * Antwort kommt (`local`/`provider`/`fallback`/`none`), und diese vier Werte
   * bleiben so. Die Frage einer Lernenden ist aber eine andere - namely ob ihre
   * Noten rausgegangen sind. Ohne dieses Feld muesste die Oberflaeche raten.
   *
   * Der lokale Weg macht diese Freigabe nicht ueberfluessig: sie gilt fuer
   * jeden Weg, auch fuer einen Server auf diesem Rechner.
   */
  grades_shared: boolean;
}

/**
 * Die Rohformen der Stammdaten-Ressourcen.
 *
 * Sie tragen exakt die Feldnamen, die `parse*` aus `lib/schemas.ts`
 * zurueckgibt. Bewusst `snake_case` und keine Umbenennung: die Namen stehen in
 * den Fehlermeldungen des Backends, die die Oberflaeche anzeigt - ein
 * umbenanntes Feld macht die Meldung unlesbar.
 */
export interface Subject {
  id: number;
  name: string;
  color: string;
  teacher_id: number | null;
  description: string | null;
  source: string;
  external_id: string | null;
  created_at: string;
}

export interface Homework {
  id: number;
  subject_id: number | null;
  subject_name: string | null;
  subject_color: string | null;
  title: string;
  description: string | null;
  due_date: string;
  completed: boolean;
  completed_at: string | null;
  source: string;
  external_id: string | null;
}

export interface Exam {
  id: number;
  subject_id: number | null;
  subject_name: string | null;
  subject_color: string | null;
  title: string;
  description: string | null;
  date: string;
  days_until: number | null;
  source: string;
  external_id: string | null;
}

export interface CalendarEvent {
  id: number;
  title: string;
  description: string | null;
  start_time: string;
  end_time: string;
  all_day: boolean;
  source: string;
  external_id: string | null;
}

/** Die vier Zustaende, die `StudyTaskStatus` im Backend kennt. */
export type StudyTaskState = "pending" | "in_progress" | "completed" | "cancelled";

/** Die Serientypen aus `StudyTaskSeries.recurrence`. */
export type StudyTaskRecurrence = "daily" | "weekly" | "biweekly" | "monthly";

export type StudyTaskOrder = "priority" | "due" | "position";

/** Hoechste Prioritaetsstufe. Spiegelt `MAX_PRIORITY` im Backend. */
export const MAX_TASK_PRIORITY = 5;

/**
 * Eine offene Vorbedingung einer Aufgabe.
 *
 * Der Titel kommt mit, damit die Oberflaeche die Warteschlange anzeigen
 * kann, ohne fuer jede Aufgabe einen zweiten Aufruf zu brauchen.
 */
export interface StudyTaskPrerequisite {
  task_id: number;
  title: string;
  status: StudyTaskState;
  due_date: string | null;
}

export interface StudyTask {
  id: number;
  study_plan_id: number;
  topic_id: number | null;
  subject_name: string | null;
  subject_color: string | null;
  topic_name: string | null;
  exam_id: number | null;
  exam_title: string | null;
  series_id: number | null;
  series_recurrence: StudyTaskRecurrence | null;
  title: string;
  description: string | null;
  duration_minutes: number;
  scheduled_for: string | null;
  due_date: string | null;
  priority: number;
  status: StudyTaskState;
  order_index: number;
  completed_at: string | null;
  blocked_by: StudyTaskPrerequisite[];
  blocking_task_ids: number[];
  /**
   * Der Schnitt des Faches, an dem die Aufgabe haengt.
   *
   * `null`, wenn es zu diesem Fach keine Noten gibt - nicht `0`, und nicht
   * ausgelassen. Der Generator nimmt diesen Wert als Mindestprioritaet, und
   * damit sichtbar ist, dass eine Prioritaet nicht nur aus der Konfidenz kam.
   */
  subject_average: number | null;
  subject_passed: boolean | null;
}

export interface StudyPlan {
  id: number;
  title: string;
  description: string | null;
  status: "active" | "paused" | "finished";
  starts_on: string | null;
  ends_on: string | null;
  task_count: number;
  open_task_count: number;
  created_at: string;
  updated_at: string;
}

export interface StudyTaskSeries {
  id: number;
  study_plan_id: number;
  topic_id: number | null;
  subject_name: string | null;
  subject_color: string | null;
  topic_name: string | null;
  title: string;
  description: string | null;
  recurrence: StudyTaskRecurrence;
  duration_minutes: number;
  priority: number;
  starts_on: string | null;
  until: string | null;
  /** Die Occurrence, die als naechstes erzeugt wird. `null` heisst: zu Ende. */
  next_due_on: string | null;
  occurrences: number;
  occurrence_limit: number | null;
  status: "active" | "finished" | "cancelled";
  created_at: string;
  updated_at: string;
}

export interface StudyTaskDependency {
  task_id: number;
  depends_on_task_id: number;
  created_at: string;
}

/** Das Ergebnis von `POST /v1/exams/{id}/tasks`. */
export interface ExamTaskPlanResult {
  exam_id: number;
  study_plan_id: number;
  created_tasks: StudyTask[];
  created_count: number;
  skipped_count: number;
  first_due_on: string | null;
  last_due_on: string | null;
}

/** Die Felder, die beim Anlegen und Bearbeiten einer Aufgabe abgeschickt werden. */
export interface StudyTaskInput {
  study_plan_id: number;
  title: string;
  topic_id?: number | null;
  description?: string | null;
  duration_minutes?: number;
  scheduled_for?: string | null;
  due_date?: string | null;
  priority?: number;
  order_index?: number;
}

export interface StudyTaskSeriesInput {
  study_plan_id: number;
  title: string;
  recurrence: StudyTaskRecurrence;
  starts_on: string;
  topic_id?: number | null;
  description?: string | null;
  duration_minutes?: number;
  priority?: number;
  until?: string | null;
  occurrence_limit?: number | null;
}

export interface StudySessionRecord {
  id: number;
  task_id: number;
  started_at: string;
  completed_at: string | null;
  duration_minutes: number | null;
  notes: string | null;
}

/**
 * Der Lernstand eines Themas, wie `GET /v1/progress/topics` ihn liefert.
 *
 * `confidence_label` und `interval_days` kommen aus **einer** Tabelle im
 * Backend. Die Oberflaeche haelt deshalb keine eigene: sonst koennten
 * Bezeichnung und Intervall auseinanderlaufen, ohne dass es auffaellt.
 *
 * `next_review_on` ist ein Datum (``YYYY-MM-DD``) in der Zeitzone des Nutzers
 * und ausdruecklich *kein* Zeitstempel. Deshalb bleibt es ein String, und
 * `new Date(...)` waere hier der erste Schritt in die falsche Zone.
 */
export interface TopicProgress {
  topic_id: number;
  name: string;
  subject_id: number | null;
  subject_name: string | null;
  confidence: number;
  /**
   * Die untere Grenze der Stufe, in die `confidence` faellt.
   *
   * Nicht dasselbe wie `confidence`: ein Aufrufer per API darf einen Wert
   * zwischen zwei Stufen setzen, und das Auswahlfeld braucht trotzdem einen
   * Wert, den es anwaehlen kann. Angezeigt wird die Stufe, denn die bestimmt
   * das Intervall.
   */
  confidence_step: number;
  confidence_label: string;
  last_reviewed_at: string | null;
  notes: string | null;
  /** Ohne `last_reviewed_at` gibt es keinen Anker und damit kein Intervall. */
  interval_days: number | null;
  next_review_on: string | null;
  days_until_review: number | null;
  is_due: boolean;
  /** Aus den Lernsessions der Aufgaben dieses Themas, nicht aus der Konfidenz. */
  studied_minutes: number;
  session_count: number;
  updated_at: string;
}

/** Eine Konfidenzstufe als Angebot fuer das Auswahlfeld. */
export interface ConfidenceStep {
  value: number;
  label: string;
  interval_days: number;
}

/** Das Wochenziel und der Ist-Stand derselben Woche (Montag bis Sonntag). */
export interface WeeklyGoal {
  goal_minutes: number;
  studied_minutes: number;
  goal_reached: boolean;
  percent: number;
  days_studied: number;
  days_remaining: number;
  week_starts_on: string;
  week_ends_on: string;
}

/** Die Serie aufeinanderfolgender Lerntage. `studied_today` heisst "heute schon". */
export interface Streak {
  current_days: number;
  longest_days: number;
  studied_today: boolean;
  last_studied_on: string | null;
  days_since_last: number | null;
  window_days: number;
}

export interface DayTotal {
  date: string;
  minutes: number;
  sessions: number;
}

/** Lernzeit je Fach. `subject_id` ist null fuer Aufgaben ohne Thema. */
export interface SubjectStudyTime {
  subject_id: number | null;
  subject_name: string;
  minutes: number;
  sessions: number;
  share_percent: number;
  last_studied_at: string | null;
}

/** `weekday` zaehlt ISO: 0 = Montag, 6 = Sonntag. */
export interface WeekdayStudyTime {
  weekday: number;
  label: string;
  minutes: number;
  sessions: number;
}

export interface LearningInsights {
  generated_at: string;
  timezone: string;
  today: DayTotal;
  week: WeeklyGoal;
  streak: Streak;
  by_subject: SubjectStudyTime[];
  by_weekday: WeekdayStudyTime[];
  /** Die Stufentabelle fuer das Auswahlfeld - vom Backend, nicht lokal. */
  confidence_steps: ConfidenceStep[];
  total_minutes: number;
  total_sessions: number;
  /** Offene Sessions zaehlen nicht in den Summen - sonst waeren sie doppelt. */
  open_sessions: number;
  active_minutes: number;
  weekday_window_days: number;
}

export interface FileUploadResult {
  id: number;
  filename: string;
  mime_type: string | null;
  path: string;
  checksum: string | null;
  status: string | null;
  created_at: string | null;
}

/** Fach-Id und Anzeigename - so weit der Picker in Formularen braucht. */
export interface SubjectOption {
  id: number;
  name: string;
}

/**
 * Die drei Arten einer Note, wie `GradeKind` im Backend.
 *
 * Eine geschlossene Liste: die Migration traegt dieselben drei Werte als
 * Check-Constraint. Der deutsche Name steht in `lib/grades.ts`, nicht hier -
 * ein Enum-Wert ist ein Vertrag, sein Anzeigename ist Darstellung.
 */
export type GradeKind = "written_exam" | "test" | "oral";

/** Eine einzelne Note, wie `GET /v1/grades` sie liefert. */
export interface Grade {
  id: number;
  subject_id: number | null;
  subject_name: string | null;
  grade_value: number;
  kind: GradeKind;
  /** Wie oft die Note in den Schnitt eingeht. 1 ist die Norm. */
  weight: number;
  /** Ein Kalendertag (`YYYY-MM-DD`), kein Zeitstempel. */
  graded_on: string;
  comment: string | null;
  source: string;
  document_id: number | null;
}

/**
 * Ein Schnitt, gewichtet.
 *
 * `passed` kommt als Wahrheitswert aus dem Backend und wird hier zu einem Wort
 * uebersetzt - "bestanden" allein waere bei 3,9 eine harte Aussage, die
 * Oberflaeche darf dazusagen, wie knapp es ist.
 */
export interface GradeAverage {
  subject_id: number | null;
  subject_name: string;
  average: number;
  count: number;
  weight: number;
  passed: boolean;
}

/** Ein Punkt der Verlaufslinie. `count` und `weight` sind kumuliert. */
export interface GradeTrendPoint {
  graded_on: string;
  average: number;
  count: number;
  weight: number;
}

/**
 * Die Hochrechnung: was ab jetzt noch kommen muss.
 *
 * `needed_average` ist `null`, wenn die noetige Note ausserhalb der Skale
 * liegt - dann gibt es keine Zahl, die man zeigen koennte, und `still_possible`
 * sagt, warum nicht.
 */
export interface GradeProjection {
  current_average: number | null;
  passing_grade: number;
  projected_weight: number;
  needed_average: number | null;
  best_possible_average: number | null;
  still_possible: boolean;
}

export interface GradeSummary {
  passing_grade: number;
  generated_at: string;
  overall: GradeAverage;
  by_subject: GradeAverage[];
  trend: GradeTrendPoint[];
  total_count: number;
  projection: GradeProjection;
}

/** Die Freigabe, ob Noten den KI-Dienst erreichen duerfen. Standard ist `false`. */
export interface GradeSharing {
  share_grades_with_ai: boolean;
  /** Wo die Entscheidung liegt - im Klartext, damit die Karte es zeigen kann. */
  preference_key: string;
  /** Wo sie ausgewertet wird. Die Liste kommt vom Backend, sie kann waachsen. */
  uses: string[];
}

/** Eine Zeile aus einem Dokument, wie der Server sie liest. */
export interface GradeImportCandidate {
  key: string;
  line_number: number;
  line: string;
  value: number;
  kind: GradeKind;
  graded_on: string;
  weight: number;
  comment: string | null;
  subject_id: number | null;
  subject_name: string | null;
  /** `false` heisst: das Fach stand nicht in der Zeile, es kam aus der Vorgabe. */
  subject_recognized: boolean;
  already_imported: boolean;
}

export interface GradeImportPreview {
  document_id: number;
  filename: string;
  subject_id: number | null;
  default_kind: GradeKind;
  default_weight: number;
  default_date: string | null;
  entries: GradeImportCandidate[];
}

export interface GradeImportResult {
  document_id: number;
  created: number;
  skipped: number;
  created_ids: number[];
}

/*
 * Belohnungen (`/v1/rewards`).
 *
 * Die Felder kommen eins zu eins aus `app/schemas/rewards.py`, und das ist hier
 * keine Formalie: die Karte zeigt einen **Zähler mit Obergrenze** und eine Liste
 * von Bedingungen. Ein `points`, das die Zod-Schemas verschlucken, wäre eine Zahl,
 * die nicht zur Anzeige passt - und eine Regel, die niemand nachrechnen kann.
 *
 * `kind` ist eine geschlossene Liste wie `GradeKind`: die Migration traegt dieselben
 * beiden Werte als Check-Constraint.
 */
export type RewardKind = "task_completed" | "subject_day";

/** Woran eine Auszeichnung haengt. Der Text dazu kommt aus `scope_kind`. */
export type RewardScopeKind = "global" | "subject" | "topic" | "week";

/** Eine gebuchte Lernhandlung. `points` kann 0 sein - das Tageskonto war voll. */
export interface RewardEntry {
  id: number;
  kind: RewardKind;
  points: number;
  /** Die angerechneten Minuten dieses Fachtags. Bei einer Aufgabe immer 0. */
  minutes: number;
  subject_id: number | null;
  subject_name: string | null;
  topic_id: number | null;
  topic_name: string | null;
  detail: string;
  /** Ein Kalendertag in der Zeitzone des Nutzers, kein Zeitstempel. */
  earned_on: string;
  earned_at: string;
}

/**
 * Eine Auszeichnung: Bedingung, Fortschritt, Zeitpunkt.
 *
 * `progress`/`target` tragen die Bedingung in Zahlen - die Karte zeigt daraus einen
 * Balken und einen Text, statt selbst zu zählen. Eine einmal verdiente Auszeichnung
 * friert bei ihrem Wert ein: `awarded` bleibt wahr, auch wenn neue Aufgaben in
 * dasselbe Fach kommen.
 */
export interface RewardBadge {
  id: number;
  code: string;
  title: string;
  description: string;
  /** `global`, `subject:7`, `topic:12` oder `week:2026-W40`. */
  scope: string;
  scope_kind: RewardScopeKind;
  subject_id: number | null;
  subject_name: string | null;
  topic_id: number | null;
  topic_name: string | null;
  progress: number;
  target: number;
  awarded: boolean;
  awarded_at: string | null;
  /** Ob die Bedingung mehrfach erreichbar ist. Steht im Katalog, nicht in einer Konvention. */
  repeatable: boolean;
  /** Was gezählt wird, in der Einzahl: "Tag", "Minute", "Aufgabe". */
  unit: string;
  /** Die Mehrzahl in der Form, die der Satz braucht: "Tagen" neben "Tag". */
  unit_plural: string;
}

/** Der Katalog als Vertrag - auch fuer Auszeichnungen, die es noch nicht gibt. */
export interface RewardBadgeDefinition {
  code: string;
  title: string;
  description: string;
  target: number;
  unit: string;
  unit_plural: string;
  scope_kind: RewardScopeKind;
  repeatable: boolean;
}

/** Eine Stufe der Fachtag-Staffel: ab so vielen Minuten gibt es so viele Punkte. */
export interface RewardStep {
  minutes: number;
  points: number;
}

/** Die Punktregel in Zahlen, damit die Karte sie nicht nachbaut. */
export interface RewardRules {
  task_points: number;
  day_cap: number;
  subject_day_cap: number;
  min_subject_day_minutes: number;
  subject_day_steps: RewardStep[];
}

/**
 * Die Zahlen des Kontos.
 *
 * `today_points` gegen `today_cap` ist die einzige Stelle, an der die Obergrenze
 * sichtbar wird. Ohne sie wäre eine Zahl, die nicht weiterwächst, eine Enttäuschung
 * statt einer Regel.
 */
export interface RewardSummary {
  generated_at: string;
  timezone: string;
  today_points: number;
  today_cap: number;
  week_points: number;
  total_points: number;
  today: string;
  week_starts_on: string;
  week_ends_on: string;
  awarded_badges: number;
  pending_badges: number;
}

/** Alles fuer die Belohnungskarte - eine Antwort, weil alles zusammen gelesen wird. */
export interface RewardOverview {
  summary: RewardSummary;
  rules: RewardRules;
  catalog: RewardBadgeDefinition[];
  badges: RewardBadge[];
  entries: RewardEntry[];
}

/*
 * Die Benachrichtigungen.
 *
 * Echte Typen, keine Stubs: bei diesen Feldern waere ein
 * `Record<string, unknown>` schlimmer als in den uebrigen Faellen, denn ein
 * `push_status`, den die Oberflaeche nicht kennt, wuerde still als "unbekannt"
 * behandelt - und eine Erinnerung, die niemand sieht, faellt nicht auf.
 */

/** Die zwei Arten, fuer die es eine Erinnerung gibt. Geschlossen, wie die Notenarten. */
export type NotificationKind = "study_task_due" | "exam_due";

/** Was aus dem Push-Versand geworden ist. `skipped` heisst: es gab keinen Weg. */
export type PushStatus = "skipped" | "sent" | "failed";

/** Die Ruhezeit als **lokale** Uhrzeiten. `spans_midnight` sagt, ob sie ueber Mitternacht laeuft. */
export interface QuietHours {
  enabled: boolean;
  start: string;
  end: string;
  spans_midnight: boolean;
}

export interface LernraumNotification {
  id: number;
  kind: NotificationKind;
  title: string;
  body: string;
  /** Ein Anker der Einzelseite, etwa `/#study-tasks`. Kein eigener Pfad. */
  url: string;
  target_id: number;
  push_status: PushStatus;
  created_at: string;
  read_at: string | null;
}

/** Die Erinnerungseinstellungen: Hauptschalter, Kanaele, Uhrzeit, Ruhezeiten. */
export interface NotificationSettings {
  enabled: boolean;
  channels: Record<string, boolean>;
  channel_labels: Record<string, string>;
  channels_available: string[];
  reminder_time: string;
  quiet_hours: QuietHours;
  exam_lead_days: number;
  timezone: string;
  preference_key: string;
  summary: string;
}

/** Der oeffentliche VAPID-Schluessel, falls der Server eines eingerichtet hat. */
export interface VapidKey {
  configured: boolean;
  public_key: string | null;
  subject: string | null;
}

export interface PushSubscriptionRecord {
  id: number;
  /** Nur der Ursprung des Endpunkts: der vollstaendige enthaelt ein Geheimnis. */
  endpoint_hint: string;
  user_agent: string | null;
  failures: number;
  last_success_at: string | null;
  last_failure_at: string | null;
  last_error: string | null;
  created_at: string;
}

export interface ReminderOutcome {
  user_id: number;
  created: number;
  delivered: number;
  push_failed: number;
  push_attempted: boolean;
  message: string;
}

/** Das Ergebnis eines Laufs - auch des, den ein Mensch ausgeloest hat. */
export interface ReminderRunReport {
  considered: number;
  created: number;
  delivered: number;
  push_failed: number;
  /** Ob der planmaessige Thread laeuft. Sonst weiss die Oberflaeche es nicht. */
  scheduler_running: boolean;
  interval_seconds: number;
  outcomes: ReminderOutcome[];
  message: string;
}

// Helper functions
function text(value: unknown, fallback: string) {  const normalized = typeof value === "string" ? value.trim() : "";
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

function normalizeBlockers(value: unknown) {
  if (!Array.isArray(value)) return [];
  return value
    .map((entry) => (typeof entry === "string" ? text(entry, "") : text((entry as { title?: unknown })?.title, "")))
    .filter((title): title is string => title.length > 0);
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
    // Eine fehlende Faelligkeit ist normal, ein unlesbares Datum nicht.
    dueDate: typeof parsed.due_date === "string" ? parsed.due_date : null,
    priority: Math.min(MAX_TASK_PRIORITY, Math.max(0, Math.round(numberValue(parsed.priority, 0)))),
    blockedBy: normalizeBlockers(parsed.blocked_by),
    isSeries: Boolean(parsed.is_series ?? parsed.series_id),
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
  // `normalizeSource` kennt den gespeicherten Altwert "ai" und behandelt alles
  // Unbekannte als "fallback": die Oberflaeche behauptet nie eine Herkunft, die
  // sie nicht belegen kann - und behauptet erst recht keine *lokale*, weil das
  // eine Datenschutzentscheidung behaupten wuerde. `none` gibt es beim Satz
  // nicht; es waere eine Kategorie zu viel.
  const quelle = normalizeSource(parsed.source);
  return {
    text: text(parsed.text, "Herzlich willkommen."),
    source: quelle === "none" ? "fallback" : quelle,
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