import { z } from "zod";

export const integrationReadSchema = z.object({
  id: z.number().int(),
  kind: z.string(),
  name: z.string(),
  enabled: z.boolean(),
  status: z.string(),
  config: z.record(z.string(), z.string()),
  has_credentials: z.boolean(),
  credential_keys: z.array(z.string()),
  missing_fields: z.array(z.string()),
  last_error: z.string().nullable().optional(),
  last_tested_at: z.string().nullable().optional(),
  last_sync_at: z.string().nullable().optional(),
  created_at: z.string(),
  updated_at: z.string(),
});

export const integrationCatalogSchema = z.object({
  kind: z.string(),
  label: z.string(),
  description: z.string(),
  required_config: z.array(z.string()),
  required_credentials: z.array(z.string()),
  optional_credentials: z.array(z.string()),
});

export const integrationTestResultSchema = z.object({
  ok: z.boolean(),
  status: z.string(),
  message: z.string(),
  missing_fields: z.array(z.string()),
});

export const integrationSyncResultSchema = integrationTestResultSchema.extend({
  imported_lessons: z.number(),
  subjects: z.number(),
  teachers: z.number(),
  rooms: z.number(),
  skipped_entries: z.number(),
  imported_documents: z.number(),
  updated_documents: z.number(),
  indexed_chunks: z.number(),
  removed_documents: z.number(),
  skipped_files: z.number(),
  window_start: z.string().nullable(),
  window_end: z.string().nullable(),
  last_sync_at: z.string().nullable(),
});

export function parseIntegrationRead(payload: unknown) {
  return integrationReadSchema.parse(payload);
}

export function parseIntegrationArray(payload: unknown) {
  return z.array(integrationReadSchema).parse(payload);
}

export function parseIntegrationCatalogArray(payload: unknown) {
  return z.array(integrationCatalogSchema).parse(payload);
}

export function parseIntegrationTestResult(payload: unknown) {
  return integrationTestResultSchema.parse(payload);
}

export function parseIntegrationSyncResult(payload: unknown) {
  return integrationSyncResultSchema.parse(payload);
}

export const documentReadSchema = z.object({
  id: z.number().int(),
  filename: z.string(),
  source: z.string(),
  status: z.string(),
  external_id: z.string().nullable().optional(),
  mime_type: z.string().nullable().optional(),
  created_at: z.string(),
  processed_at: z.string().nullable().optional(),
});

export const documentSourceSchema = z.object({
  document_id: z.number().int(),
  filename: z.string(),
  chunk_index: z.number().int(),
  content: z.string(),
});

export const documentHitSchema = documentSourceSchema.extend({
  distance: z.number().nullable().optional(),
});

export const documentSearchResponseSchema = z.object({
  query: z.string(),
  hits: z.array(documentHitSchema),
});

export const documentAskResponseSchema = z.object({
  answer: z.string(),
  source: z.string(),
  model: z.string().nullable().optional(),
  sources: z.array(documentSourceSchema),
  // Neu gegenueber aelteren Backends. `.default(false)` haelt die Karte
  // lauffaehig, wenn ein Server ohne Noten antwortet - und "aus" ist der
  // sichere Fall, nicht der bemerkenswerte.
  grades_shared: z.boolean().default(false),
});

export function parseDocumentArray(payload: unknown) {
  return z.array(documentReadSchema).parse(payload).map((entry) => ({
    id: entry.id,
    filename: entry.filename,
    source: entry.source,
    status: entry.status,
    external_id: entry.external_id ?? null,
    mime_type: entry.mime_type ?? null,
    created_at: entry.created_at,
    processed_at: entry.processed_at ?? null,
  }));
}

export function parseDocumentSearchResult(payload: unknown) {
  const parsed = documentSearchResponseSchema.parse(payload);
  return {
    query: parsed.query,
    hits: parsed.hits.map((hit) => ({
      document_id: hit.document_id,
      filename: hit.filename,
      chunk_index: hit.chunk_index,
      content: hit.content,
      distance: hit.distance ?? null,
    })),
  };
}

export function parseDocumentAnswer(payload: unknown) {
  const parsed = documentAskResponseSchema.parse(payload);
  return {
    answer: parsed.answer,
    // Das Backend kennt "ai", "fallback" und "none"; alles andere wird als
    // Fallback behandelt, damit die Oberflaeche nie eine falsche Herkunft behauptet.
    source: (parsed.source === "ai" || parsed.source === "none" ? parsed.source : "fallback") as "ai" | "fallback" | "none",
    model: parsed.model ?? null,
    sources: parsed.sources,
    // Kommt als eigenes Feld aus dem Backend: `source` bleibt dreiwertig.
    grades_shared: parsed.grades_shared,
  };
}

/*
 * Akademische Stammdaten und Aufgaben.
 *
 * Anders als die weiter unten liegenden Blöcke sind das *echte* Schemata: sie
 * validieren, was das Backend garantiert. `extra="forbid"` bleibt hier
 * bewusst weg, weil die Antworten des Backends je nach Version Felder
 * ergänzen dürfen, ohne dass die Oberfläche daran bricht - `passthrough()`
 * wäre aber auch nicht richtig, denn dann prüft nichts.
 */

/** Ein optionaler Text: `null` und fehlende Angabe sind dasselbe. */
const optionalText = z.string().nullable().optional();
/** Ein optionaler Bezug auf ein anderes Objekt. */
const optionalId = z.number().int().nullable().optional();

export const subjectReadSchema = z.object({
  id: z.number().int(),
  name: z.string(),
  color: z.string(),
  teacher_id: optionalId,
  description: optionalText,
  source: z.string(),
  external_id: optionalText,
  created_at: z.string(),
});

export const homeworkReadSchema = z.object({
  id: z.number().int(),
  subject_id: optionalId,
  subject_name: optionalText,
  subject_color: optionalText,
  title: z.string(),
  description: optionalText,
  due_date: z.string(),
  completed: z.boolean(),
  completed_at: optionalText,
  source: z.string(),
  external_id: optionalText,
});

export const examReadSchema = z.object({
  id: z.number().int(),
  subject_id: optionalId,
  subject_name: optionalText,
  subject_color: optionalText,
  title: z.string(),
  description: optionalText,
  date: z.string(),
  days_until: z.number().int().nullable().optional(),
  source: z.string(),
  external_id: optionalText,
});

export const calendarEventReadSchema = z.object({
  id: z.number().int(),
  title: z.string(),
  description: optionalText,
  start_time: z.string(),
  end_time: z.string(),
  all_day: z.boolean(),
  source: z.string(),
  external_id: optionalText,
});

/** `pending | in_progress | completed | cancelled` - siehe `StudyTaskStatus` im Backend. */
export const studyTaskStatusSchema = z.enum(["pending", "in_progress", "completed", "cancelled"]);

/** Die Serientypen aus `StudyTaskSeries.recurrence`. */
export const studyTaskRecurrenceSchema = z.enum(["daily", "weekly", "biweekly", "monthly"]);

/**
 * Eine offene Vorbedingung.
 *
 * `task_id` braucht die Oberflaeche, um die Verknuepfung ohne einen zweiten
 * Aufruf wieder loeschen zu koennen.
 */
export const studyTaskPrerequisiteSchema = z.object({
  task_id: z.number().int(),
  title: z.string(),
  status: studyTaskStatusSchema,
  due_date: optionalText,
});

export const studyTaskReadSchema = z.object({
  id: z.number().int(),
  study_plan_id: z.number().int(),
  topic_id: optionalId,
  subject_name: optionalText,
  subject_color: optionalText,
  topic_name: optionalText,
  exam_id: optionalId,
  exam_title: optionalText,
  series_id: optionalId,
  series_recurrence: studyTaskRecurrenceSchema.nullable().optional(),
  title: z.string(),
  description: optionalText,
  duration_minutes: z.number().int(),
  scheduled_for: optionalText,
  due_date: optionalText,
  priority: z.number().int(),
  status: studyTaskStatusSchema,
  order_index: z.number().int(),
  completed_at: optionalText,
  blocked_by: z.array(studyTaskPrerequisiteSchema),
  blocking_task_ids: z.array(z.number().int()),
  // Der Fachschnitt, an dem die Prioritaet dieser Aufgabe haengt. Bewusst
  // nullable: ein Fach ohne Noten hat keinen Schnitt, und `0` waere eine
  // schlechte Note.
  subject_average: z.number().nullable().optional(),
  subject_passed: z.boolean().nullable().optional(),
});

export const studyPlanReadSchema = z.object({
  id: z.number().int(),
  title: z.string(),
  description: optionalText,
  status: z.enum(["active", "paused", "finished"]),
  starts_on: optionalText,
  ends_on: optionalText,
  task_count: z.number().int(),
  open_task_count: z.number().int(),
  created_at: z.string(),
  updated_at: z.string(),
});

export const studyTaskSeriesReadSchema = z.object({
  id: z.number().int(),
  study_plan_id: z.number().int(),
  topic_id: optionalId,
  subject_name: optionalText,
  subject_color: optionalText,
  topic_name: optionalText,
  title: z.string(),
  description: optionalText,
  recurrence: studyTaskRecurrenceSchema,
  duration_minutes: z.number().int(),
  priority: z.number().int(),
  starts_on: optionalText,
  until: optionalText,
  next_due_on: optionalText,
  occurrences: z.number().int(),
  occurrence_limit: z.number().int().nullable().optional(),
  status: z.enum(["active", "finished", "cancelled"]),
  created_at: z.string(),
  updated_at: z.string(),
});

export const studyTaskDependencyReadSchema = z.object({
  task_id: z.number().int(),
  depends_on_task_id: z.number().int(),
  created_at: z.string(),
});

export const examTaskPlanResultSchema = z.object({
  exam_id: z.number().int(),
  study_plan_id: z.number().int(),
  created_tasks: z.array(studyTaskReadSchema),
  created_count: z.number().int(),
  skipped_count: z.number().int(),
  first_due_on: optionalText,
  last_due_on: optionalText,
});

/** Antwort von `POST /v1/study/tasks/{id}/start` - die einzige Aktion mit eigener Form. */
export const studySessionReadSchema = z.object({
  id: z.number().int(),
  task_id: z.number().int(),
  started_at: z.string(),
  completed_at: optionalText,
  duration_minutes: z.number().int().nullable().optional(),
  notes: optionalText,
});

/*
 * Lernfortschritt und Lernprotokoll (`/v1/progress`).
 *
 * Auch das sind echte Schemata: die Karten zeigen Konfidenz, Lernzeit und
 * einen Wiederholungstermin an, und eine Zahl, die stillschweigend fehlt,
 * waere dort ein falscher Termin. `next_review_on` ist bewusst `string` und
 * kein `date`: es kommt als `YYYY-MM-DD` und wird **nicht** als Zeitstempel
 * geparst - ein Datum in der Zeitzone des Nutzers ist kein UTC-Moment.
 */

/** `YYYY-MM-DD` aus dem Backend, etwa der Tag der naechsten Wiederholung. */
const isoDate = z.string();

export const topicProgressReadSchema = z.object({
  topic_id: z.number().int(),
  name: z.string(),
  subject_id: optionalId,
  subject_name: optionalText,
  confidence: z.number().min(0).max(1),
  confidence_step: z.number().min(0).max(1),
  // Die Bezeichnung kommt vom Backend (`CONFIDENCE_STEPS`). Sie steht hier
  // bewusst nicht noch einmal als Liste - sonst koennten beide Enden
  // auseinanderlaufen.
  confidence_label: z.string(),
  last_reviewed_at: optionalText,
  notes: optionalText,
  interval_days: z.number().int().nullable(),
  next_review_on: isoDate.nullable(),
  days_until_review: z.number().int().nullable(),
  is_due: z.boolean(),
  studied_minutes: z.number().int(),
  session_count: z.number().int(),
  updated_at: z.string(),
});

export const confidenceStepSchema = z.object({
  value: z.number().min(0).max(1),
  label: z.string(),
  interval_days: z.number().int(),
});

export const weeklyGoalReadSchema = z.object({
  goal_minutes: z.number().int(),
  studied_minutes: z.number().int(),
  goal_reached: z.boolean(),
  percent: z.number(),
  days_studied: z.number().int(),
  days_remaining: z.number().int(),
  week_starts_on: isoDate,
  week_ends_on: isoDate,
});

export const streakReadSchema = z.object({
  current_days: z.number().int(),
  longest_days: z.number().int(),
  studied_today: z.boolean(),
  last_studied_on: isoDate.nullable(),
  days_since_last: z.number().int().nullable(),
  window_days: z.number().int(),
});

export const dayTotalSchema = z.object({
  date: isoDate,
  minutes: z.number().int(),
  sessions: z.number().int(),
});

export const subjectStudyTimeSchema = z.object({
  subject_id: optionalId,
  subject_name: z.string(),
  minutes: z.number().int(),
  sessions: z.number().int(),
  share_percent: z.number(),
  last_studied_at: optionalText,
});

export const weekdayStudyTimeSchema = z.object({
  weekday: z.number().int(),
  label: z.string(),
  minutes: z.number().int(),
  sessions: z.number().int(),
});

export const learningInsightsSchema = z.object({
  generated_at: z.string(),
  timezone: z.string(),
  today: dayTotalSchema,
  week: weeklyGoalReadSchema,
  streak: streakReadSchema,
  by_subject: z.array(subjectStudyTimeSchema),
  by_weekday: z.array(weekdayStudyTimeSchema),
  confidence_steps: z.array(confidenceStepSchema),
  total_minutes: z.number().int(),
  total_sessions: z.number().int(),
  open_sessions: z.number().int(),
  active_minutes: z.number().int(),
  weekday_window_days: z.number().int(),
});

/*
 * Noten (`/v1/grades`).
 *
 * Auch das sind echte Schemata. Der Grund ist derselbe wie beim Lernfortschritt:
 * die Karte zeigt einen Schnitt und eine Linie an, und eine Zahl, die
 * stillschweigend fehlt, waere dort eine erfundene. `subject_average` ist
 * bewusst `number | null` und nicht `number`: eine Note ohne Fachschnitt gibt es
 * nicht, und eine `0` waere eine schlechte Note.
 */

export const gradeKindSchema = z.enum(["written_exam", "test", "oral"]);

export const gradeReadSchema = z.object({
  id: z.number().int(),
  subject_id: optionalId,
  subject_name: optionalText,
  grade_value: z.number().positive().max(6),
  kind: gradeKindSchema,
  weight: z.number().int().min(1),
  graded_on: isoDate,
  comment: optionalText,
  source: z.string(),
  document_id: optionalId,
});

export const gradeAverageSchema = z.object({
  subject_id: optionalId,
  subject_name: z.string(),
  average: z.number().min(0),
  count: z.number().int().min(0),
  weight: z.number().int().min(0),
  passed: z.boolean(),
});

export const gradeTrendPointSchema = z.object({
  graded_on: isoDate,
  average: z.number().min(0),
  count: z.number().int().min(1),
  weight: z.number().int().min(1),
});

export const gradeProjectionSchema = z.object({
  current_average: z.number().nullable(),
  passing_grade: z.number(),
  projected_weight: z.number().int().min(0),
  needed_average: z.number().nullable(),
  best_possible_average: z.number().nullable(),
  still_possible: z.boolean(),
});

export const gradeSummarySchema = z.object({
  passing_grade: z.number(),
  generated_at: z.string(),
  overall: gradeAverageSchema,
  by_subject: z.array(gradeAverageSchema),
  trend: z.array(gradeTrendPointSchema),
  total_count: z.number().int().min(0),
  projection: gradeProjectionSchema,
});

export const gradeSharingSchema = z.object({
  share_grades_with_ai: z.boolean(),
  preference_key: z.string(),
  uses: z.array(z.string()),
});

export const gradeImportCandidateSchema = z.object({
  key: z.string().min(1),
  line_number: z.number().int().min(1),
  line: z.string(),
  value: z.number().positive().max(6),
  kind: gradeKindSchema,
  graded_on: isoDate,
  weight: z.number().int().min(1),
  comment: optionalText,
  subject_id: optionalId,
  subject_name: optionalText,
  subject_recognized: z.boolean(),
  already_imported: z.boolean(),
});

export const gradeImportPreviewSchema = z.object({
  document_id: z.number().int(),
  filename: z.string(),
  subject_id: optionalId,
  default_kind: gradeKindSchema,
  default_weight: z.number().int().min(1),
  default_date: z.string().nullable().optional(),
  entries: z.array(gradeImportCandidateSchema),
});

export const gradeImportResultSchema = z.object({
  document_id: z.number().int(),
  created: z.number().int().min(0),
  skipped: z.number().int().min(0),
  created_ids: z.array(z.number().int()),
});

export function parseGradeArray(payload: unknown) {
  return z.array(gradeReadSchema).parse(payload).map((entry) => ({
    id: entry.id,
    subject_id: entry.subject_id ?? null,
    subject_name: entry.subject_name ?? null,
    grade_value: entry.grade_value,
    kind: entry.kind,
    weight: entry.weight,
    graded_on: entry.graded_on,
    comment: entry.comment ?? null,
    source: entry.source,
    document_id: entry.document_id ?? null,
  }));
}

export function parseGradeRead(payload: unknown) {
  return parseGradeArray([payload])[0];
}

export function parseGradeSummary(payload: unknown) {
  const parsed = gradeSummarySchema.parse(payload);
  return {
    passing_grade: parsed.passing_grade,
    generated_at: parsed.generated_at,
    overall: parseGradeAverage(parsed.overall),
    by_subject: parsed.by_subject.map(parseGradeAverage),
    trend: parsed.trend.map((punkt) => ({
      graded_on: punkt.graded_on,
      average: punkt.average,
      count: punkt.count,
      weight: punkt.weight,
    })),
    total_count: parsed.total_count,
    projection: {
      current_average: parsed.projection.current_average ?? null,
      passing_grade: parsed.projection.passing_grade,
      projected_weight: parsed.projection.projected_weight,
      needed_average: parsed.projection.needed_average ?? null,
      best_possible_average: parsed.projection.best_possible_average ?? null,
      still_possible: parsed.projection.still_possible,
    },
  };
}

function parseGradeAverage(entry: z.infer<typeof gradeAverageSchema>) {
  return {
    subject_id: entry.subject_id ?? null,
    subject_name: entry.subject_name,
    average: entry.average,
    count: entry.count,
    weight: entry.weight,
    passed: entry.passed,
  };
}

export function parseGradeSharing(payload: unknown) {
  const parsed = gradeSharingSchema.parse(payload);
  return {
    share_grades_with_ai: parsed.share_grades_with_ai,
    preference_key: parsed.preference_key,
    uses: parsed.uses,
  };
}

export function parseGradeImportPreview(payload: unknown) {
  const parsed = gradeImportPreviewSchema.parse(payload);
  return {
    document_id: parsed.document_id,
    filename: parsed.filename,
    subject_id: parsed.subject_id ?? null,
    default_kind: parsed.default_kind,
    default_weight: parsed.default_weight,
    default_date: parsed.default_date ?? null,
    entries: parsed.entries.map((eintrag) => ({
      key: eintrag.key,
      line_number: eintrag.line_number,
      line: eintrag.line,
      value: eintrag.value,
      kind: eintrag.kind,
      graded_on: eintrag.graded_on,
      weight: eintrag.weight,
      comment: eintrag.comment ?? null,
      subject_id: eintrag.subject_id ?? null,
      subject_name: eintrag.subject_name ?? null,
      subject_recognized: eintrag.subject_recognized,
      already_imported: eintrag.already_imported,
    })),
  };
}

export function parseGradeImportResult(payload: unknown) {
  const parsed = gradeImportResultSchema.parse(payload);
  return {
    document_id: parsed.document_id,
    created: parsed.created,
    skipped: parsed.skipped,
    created_ids: parsed.created_ids,
  };
}

export const fileUploadResponseSchema = z.object({
  id: z.number().int(),
  filename: z.string(),
  mime_type: optionalText,
  path: z.string(),
  checksum: optionalText,
  status: optionalText,
  created_at: optionalText,
});

/*
 * Die Benachrichtigungen.
 *
 * Echte Schemata, keine Stubs: bei diesen Feldern waere ein
 * `z.object({}).passthrough()` schlimmer als in den uebrigen Faellen, denn ein
 * `push_status`, den die Oberflaeche nicht kennt, wuerde still als "unbekannt"
 * behandelt - und eine Erinnerung, die niemand sieht, faellt nicht auf.
 */

export const notificationKindSchema = z.enum(["study_task_due", "exam_due"]);
export const pushStatusSchema = z.enum(["skipped", "sent", "failed"]);

export const notificationReadSchema = z.object({
  id: z.number().int(),
  kind: notificationKindSchema,
  title: z.string(),
  body: z.string(),
  url: z.string(),
  target_id: z.number().int(),
  push_status: pushStatusSchema,
  created_at: z.string(),
  read_at: z.string().nullable(),
});

export const quietHoursSchema = z.object({
  enabled: z.boolean(),
  start: z.string(),
  end: z.string(),
  spans_midnight: z.boolean(),
});

export const notificationSettingsSchema = z.object({
  enabled: z.boolean(),
  channels: z.record(z.string(), z.boolean()),
  channel_labels: z.record(z.string(), z.string()),
  channels_available: z.array(z.string()),
  reminder_time: z.string(),
  quiet_hours: quietHoursSchema,
  exam_lead_days: z.number().int(),
  timezone: z.string(),
  preference_key: z.string(),
  summary: z.string(),
});

export const vapidKeySchema = z.object({
  configured: z.boolean(),
  public_key: z.string().nullable(),
  subject: z.string().nullable(),
});

export const pushSubscriptionSchema = z.object({
  id: z.number().int(),
  endpoint_hint: z.string(),
  user_agent: z.string().nullable(),
  failures: z.number().int(),
  last_success_at: z.string().nullable(),
  last_failure_at: z.string().nullable(),
  last_error: z.string().nullable(),
  created_at: z.string(),
});

export const reminderRunSchema = z.object({
  considered: z.number().int(),
  created: z.number().int(),
  delivered: z.number().int(),
  push_failed: z.number().int(),
  scheduler_running: z.boolean(),
  interval_seconds: z.number(),
  outcomes: z.array(
    z.object({
      user_id: z.number().int(),
      created: z.number().int(),
      delivered: z.number().int(),
      push_failed: z.number().int(),
      push_attempted: z.boolean(),
      message: z.string(),
    }),
  ),
  message: z.string(),
});

export function parseNotificationArray(payload: unknown) {
  return z.array(notificationReadSchema).parse(payload);
}

export function parseNotificationSettings(payload: unknown) {
  return notificationSettingsSchema.parse(payload);
}

export function parseVapidKey(payload: unknown) {
  return vapidKeySchema.parse(payload);
}

export function parsePushSubscriptionArray(payload: unknown) {
  return z.array(pushSubscriptionSchema).parse(payload);
}

export function parsePushSubscription(payload: unknown) {
  return pushSubscriptionSchema.parse(payload);
}

/** `POST /v1/notifications/read` antwortet nur mit der Zahl der quittierten Zeilen. */
export const markReadResultSchema = z.object({ read: z.number().int() });

export function parseMarkReadResult(payload: unknown) {
  return markReadResultSchema.parse(payload).read;
}

export function parseReminderRun(payload: unknown) {
  return reminderRunSchema.parse(payload);
}


export function parseSubjectArray(payload: unknown) {
  return z.array(subjectReadSchema).parse(payload).map((entry) => ({
    id: entry.id,
    name: entry.name,
    color: entry.color,
    teacher_id: entry.teacher_id ?? null,
    description: entry.description ?? null,
    source: entry.source,
    external_id: entry.external_id ?? null,
    created_at: entry.created_at,
  }));
}

export function parseSubjectRead(payload: unknown) {
  return parseSubjectArray([payload])[0];
}

export function parseHomeworkArray(payload: unknown) {
  return z.array(homeworkReadSchema).parse(payload).map((entry) => ({
    id: entry.id,
    subject_id: entry.subject_id ?? null,
    subject_name: entry.subject_name ?? null,
    subject_color: entry.subject_color ?? null,
    title: entry.title,
    description: entry.description ?? null,
    due_date: entry.due_date,
    completed: entry.completed,
    completed_at: entry.completed_at ?? null,
    source: entry.source,
    external_id: entry.external_id ?? null,
  }));
}

export function parseHomeworkRead(payload: unknown) {
  return parseHomeworkArray([payload])[0];
}

export function parseExamArray(payload: unknown) {
  return z.array(examReadSchema).parse(payload).map((entry) => ({
    id: entry.id,
    subject_id: entry.subject_id ?? null,
    subject_name: entry.subject_name ?? null,
    subject_color: entry.subject_color ?? null,
    title: entry.title,
    description: entry.description ?? null,
    date: entry.date,
    days_until: entry.days_until ?? null,
    source: entry.source,
    external_id: entry.external_id ?? null,
  }));
}

export function parseExamRead(payload: unknown) {
  return parseExamArray([payload])[0];
}

export function parseCalendarEventArray(payload: unknown) {
  return z.array(calendarEventReadSchema).parse(payload).map((entry) => ({
    id: entry.id,
    title: entry.title,
    description: entry.description ?? null,
    start_time: entry.start_time,
    end_time: entry.end_time,
    all_day: entry.all_day,
    source: entry.source,
    external_id: entry.external_id ?? null,
  }));
}

export function parseCalendarEventRead(payload: unknown) {
  return parseCalendarEventArray([payload])[0];
}

export function parseStudyTaskArray(payload: unknown) {
  return z.array(studyTaskReadSchema).parse(payload).map((entry) => ({
    id: entry.id,
    study_plan_id: entry.study_plan_id,
    topic_id: entry.topic_id ?? null,
    subject_name: entry.subject_name ?? null,
    subject_color: entry.subject_color ?? null,
    topic_name: entry.topic_name ?? null,
    exam_id: entry.exam_id ?? null,
    exam_title: entry.exam_title ?? null,
    series_id: entry.series_id ?? null,
    series_recurrence: entry.series_recurrence ?? null,
    title: entry.title,
    description: entry.description ?? null,
    duration_minutes: entry.duration_minutes,
    scheduled_for: entry.scheduled_for ?? null,
    due_date: entry.due_date ?? null,
    priority: entry.priority,
    status: entry.status,
    order_index: entry.order_index,
    completed_at: entry.completed_at ?? null,
    // Die Abhaengigkeiten sind Pflicht im Task-Manager, deshalb steht hier
    // bewusst kein `?? []`: eine Antwort ohne sie waere ein Rueckwaerts-
    // kompatibilitaetsfehler und soll auffallen.
    blocked_by: entry.blocked_by.map((prerequisite) => ({
      task_id: prerequisite.task_id,
      title: prerequisite.title,
      status: prerequisite.status,
      due_date: prerequisite.due_date ?? null,
    })),
    blocking_task_ids: entry.blocking_task_ids,
    // Der Generator nimmt den Fachschnitt als Mindestprioritaet; die Karte
    // zeigt, dass eine Prioritaet nicht nur aus der Konfidenz kam.
    subject_average: entry.subject_average ?? null,
    subject_passed: entry.subject_passed ?? null,
  }));
}

export function parseStudyTaskRead(payload: unknown) {
  return parseStudyTaskArray([payload])[0];
}

export function parseStudyPlanArray(payload: unknown) {
  return z.array(studyPlanReadSchema).parse(payload).map((entry) => ({
    id: entry.id,
    title: entry.title,
    description: entry.description ?? null,
    status: entry.status,
    starts_on: entry.starts_on ?? null,
    ends_on: entry.ends_on ?? null,
    task_count: entry.task_count,
    open_task_count: entry.open_task_count,
    created_at: entry.created_at,
    updated_at: entry.updated_at,
  }));
}

export function parseStudyPlanRead(payload: unknown) {
  return parseStudyPlanArray([payload])[0];
}

export function parseStudySeriesArray(payload: unknown) {
  return z.array(studyTaskSeriesReadSchema).parse(payload).map((entry) => ({
    id: entry.id,
    study_plan_id: entry.study_plan_id,
    topic_id: entry.topic_id ?? null,
    subject_name: entry.subject_name ?? null,
    subject_color: entry.subject_color ?? null,
    topic_name: entry.topic_name ?? null,
    title: entry.title,
    description: entry.description ?? null,
    recurrence: entry.recurrence,
    duration_minutes: entry.duration_minutes,
    priority: entry.priority,
    starts_on: entry.starts_on ?? null,
    until: entry.until ?? null,
    next_due_on: entry.next_due_on ?? null,
    occurrences: entry.occurrences,
    occurrence_limit: entry.occurrence_limit ?? null,
    status: entry.status,
    created_at: entry.created_at,
    updated_at: entry.updated_at,
  }));
}

export function parseStudySeriesRead(payload: unknown) {
  return parseStudySeriesArray([payload])[0];
}

export function parseStudyDependencyArray(payload: unknown) {
  return z.array(studyTaskDependencyReadSchema).parse(payload);
}

export function parseStudyDependencyRead(payload: unknown) {
  return studyTaskDependencyReadSchema.parse(payload);
}

export function parseExamTaskPlanResult(payload: unknown) {
  const parsed = examTaskPlanResultSchema.parse(payload);
  return {
    exam_id: parsed.exam_id,
    study_plan_id: parsed.study_plan_id,
    created_tasks: parseStudyTaskArray(parsed.created_tasks),
    created_count: parsed.created_count,
    skipped_count: parsed.skipped_count,
    first_due_on: parsed.first_due_on ?? null,
    last_due_on: parsed.last_due_on ?? null,
  };
}

export function parseStudySessionRead(payload: unknown) {
  const parsed = studySessionReadSchema.parse(payload);
  return {
    id: parsed.id,
    task_id: parsed.task_id,
    started_at: parsed.started_at,
    completed_at: parsed.completed_at ?? null,
    duration_minutes: parsed.duration_minutes ?? null,
    notes: parsed.notes ?? null,
  };
}

export function parseTopicProgressArray(payload: unknown) {
  return z.array(topicProgressReadSchema).parse(payload).map((entry) => ({
    topic_id: entry.topic_id,
    name: entry.name,
    subject_id: entry.subject_id ?? null,
    subject_name: entry.subject_name ?? null,
    confidence: entry.confidence,
    confidence_step: entry.confidence_step,
    confidence_label: entry.confidence_label,
    last_reviewed_at: entry.last_reviewed_at ?? null,
    notes: entry.notes ?? null,
    interval_days: entry.interval_days,
    next_review_on: entry.next_review_on,
    days_until_review: entry.days_until_review,
    is_due: entry.is_due,
    studied_minutes: entry.studied_minutes,
    session_count: entry.session_count,
    updated_at: entry.updated_at,
  }));
}

export function parseTopicProgressRead(payload: unknown) {
  return parseTopicProgressArray([payload])[0];
}

export function parseWeeklyGoalRead(payload: unknown) {
  const parsed = weeklyGoalReadSchema.parse(payload);
  return {
    goal_minutes: parsed.goal_minutes,
    studied_minutes: parsed.studied_minutes,
    goal_reached: parsed.goal_reached,
    percent: parsed.percent,
    days_studied: parsed.days_studied,
    days_remaining: parsed.days_remaining,
    week_starts_on: parsed.week_starts_on,
    week_ends_on: parsed.week_ends_on,
  };
}

export function parseLearningInsights(payload: unknown) {
  const parsed = learningInsightsSchema.parse(payload);
  return {
    generated_at: parsed.generated_at,
    timezone: parsed.timezone,
    today: { date: parsed.today.date, minutes: parsed.today.minutes, sessions: parsed.today.sessions },
    week: parseWeeklyGoalRead(parsed.week),
    streak: {
      current_days: parsed.streak.current_days,
      longest_days: parsed.streak.longest_days,
      studied_today: parsed.streak.studied_today,
      last_studied_on: parsed.streak.last_studied_on,
      days_since_last: parsed.streak.days_since_last ?? null,
      window_days: parsed.streak.window_days,
    },
    by_subject: parsed.by_subject.map((entry) => ({
      subject_id: entry.subject_id ?? null,
      subject_name: entry.subject_name,
      minutes: entry.minutes,
      sessions: entry.sessions,
      share_percent: entry.share_percent,
      last_studied_at: entry.last_studied_at ?? null,
    })),
    by_weekday: parsed.by_weekday.map((entry) => ({
      weekday: entry.weekday,
      label: entry.label,
      minutes: entry.minutes,
      sessions: entry.sessions,
    })),
    confidence_steps: parsed.confidence_steps.map((entry) => ({
      value: entry.value,
      label: entry.label,
      interval_days: entry.interval_days,
    })),
    total_minutes: parsed.total_minutes,
    total_sessions: parsed.total_sessions,
    open_sessions: parsed.open_sessions,
    active_minutes: parsed.active_minutes,
    weekday_window_days: parsed.weekday_window_days,
  };
}

export function parseFileUploadResponse(payload: unknown) {
  const parsed = fileUploadResponseSchema.parse(payload);
  return {
    id: parsed.id,
    filename: parsed.filename,
    mime_type: parsed.mime_type ?? null,
    path: parsed.path,
    checksum: parsed.checksum ?? null,
    status: parsed.status ?? null,
    created_at: parsed.created_at ?? null,
  };
}

/**
 * Die Fach-Ids, die in Aufgaben und Terminen auftauchen.
 *
 * Die Lernenden pflegen Fachkuerzel und Hausaufgaben ueber getrennte Formulare;
 * fuer den Picker reicht die Liste, die ohnehin geladen ist. Ohne Fach gibt es
 * `[]` - das ist kein Fehler, sondern der Zustand eines frischen Kontos.
 */
export function collectSubjectNames(payload: unknown): Array<{ id: number; name: string }> {
  if (!Array.isArray(payload)) {
    return [];
  }
  const byId = new Map<number, string>();
  for (const entry of payload) {
    const parsed = subjectReadSchema.safeParse(entry);
    if (parsed.success) {
      byId.set(parsed.data.id, parsed.data.name);
    }
  }
  return [...byId.entries()]
    .map(([id, name]) => ({ id, name }))
    .sort((a, b) => a.name.localeCompare(b.name, "de"));
}

// Minimal passthrough schemas for other data structures
export const dashboardApiResponseSchema = z.object({}).passthrough();
export type DashboardApiResponse = z.infer<typeof dashboardApiResponseSchema>;
export function parseDashboardApiResponse(payload: unknown): any {
  return dashboardApiResponseSchema.parse(payload);
}

export const scheduleRangeResponseSchema = z.object({}).passthrough();
export type ScheduleRangeResponse = z.infer<typeof scheduleRangeResponseSchema>;
// Bewusst kein `any` wie bei den Nachbarn: der Aufrufer normalisiert über `record()` in lib/types.ts.
export function parseScheduleRangeResponse(payload: unknown): unknown {
  return scheduleRangeResponseSchema.parse(payload);
}

export const greetingResponseSchema = z.object({}).passthrough();
export type GreetingResponse = z.infer<typeof greetingResponseSchema>;
// Bewusst kein `any`: der Aufrufer normalisiert über `record()` in lib/types.ts.
export function parseGreetingResponse(payload: unknown): unknown {
  return greetingResponseSchema.parse(payload);
}

export const studySessionSchema = z.object({}).passthrough();
export type StudySession = z.infer<typeof studySessionSchema>;
export function parseStudySession(payload: unknown): any {
  return studySessionSchema.parse(payload);
}

export const studyTaskSchema = z.object({}).passthrough();
export type StudyTaskPayload = z.infer<typeof studyTaskSchema>;
export function parseStudyTask(payload: unknown): any {
  return studyTaskSchema.parse(payload);
}

export const studyActionResponseSchema = z.object({}).passthrough();
export function parseStudyActionResponse(payload: unknown): any {
  return studyActionResponseSchema.parse(payload);
}

export const loginResponseSchema = z.object({}).passthrough();
export function parseLoginResponse(payload: unknown): any {
  return loginResponseSchema.parse(payload);
}

export const userResponseSchema = z.object({}).passthrough();
export function parseUserResponse(payload: unknown): any {
  return userResponseSchema.parse(payload);
}

export const userSessionSchema = z.object({}).passthrough();
export function parseUserSession(payload: unknown): any {
  return userSessionSchema.parse(payload);
}
