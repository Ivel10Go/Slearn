"use client";

import { getDemoDashboard, getDemoScheduleRange, isDemoFallbackEnabled } from "./demo";
import { parseAiRuntime, type AiRuntimePayload } from "./ai-runtime";
import { endungFuerFormat, parseSpeechCapabilities, parseVoiceList, type SpeechCapabilitiesPayload, type VoiceListPayload } from "./speech";
import { isDemoTaskId, normalizeDashboard, normalizeGreeting, normalizeScheduleRange, normalizeStudyAction, type ApiIdentifier, type CalendarEvent, type DashboardData, type DocumentAnswer, type DocumentRecord, type DocumentSearchResult, type Exam, type FileUploadResult, type Grade, type GradeImportPreview, type GradeImportResult, type GradeKind, type GradeSharing, type GradeSummary, type Greeting, type Homework, type ScheduleRange, type StudySessionRecord, type StudyTask, type StudyTaskState, type Subject, type SubjectOption } from "./types";
import { collectSubjectNames, parseCalendarEventArray, parseCalendarEventRead, parseCalendarFeed, parseCalendarWriting, parseDocumentAnswer, parseDocumentArray, parseDocumentSearchResult, parseExamArray, parseExamRead, parseExamTaskPlanResult, parseFileUploadResponse, parseGradeArray, parseGradeImportPreview, parseGradeImportResult, parseGradeRead, parseGradeSharing, parseGradeSummary, parseHomeworkArray, parseHomeworkRead, parseIntegrationArray, parseIntegrationAuthorize, parseIntegrationCatalogArray, parseIntegrationRead, parseIntegrationSyncResult, parseIntegrationTestResult, parseLearningInsights, parseMarkReadResult, parseNotificationArray, parseNotificationSettings, parsePushSubscription, parsePushSubscriptionArray, parseReminderRun, parseRewardOverview, parseStudyDependencyArray, parseStudyDependencyRead, parseStudyPlanArray, parseStudyPlanRead, parseStudySeriesArray, parseStudySeriesRead, parseStudySessionRead, parseStudyTaskArray, parseStudyTaskRead, parseSubjectArray, parseSubjectRead, parseTopicProgressArray, parseTopicProgressRead, parseUserResponse, parseUserSession, parseVapidKey, parseWeeklyGoalRead } from "./schemas";
import type { CalendarFeed, CalendarWriting, ExamTaskPlanResult, IntegrationAuthorize, IntegrationCatalog, IntegrationRead, IntegrationSyncResult, IntegrationTestResult, LearningInsights, MaterialKind, RewardOverview, StudyPlan, StudyTaskDependency, StudyTaskOrder, StudyTaskSeries, StudyTaskSeriesInput, TopicProgress, WeeklyGoal } from "./types";
import { getLocale, t, translateServerText } from "./i18n";

const API_PREFIX = "/api/backend";

/**
 * Was die Oberflaeche annimmt, wenn der Server gar nicht antwortet.
 *
 * Der Textmodus ist hier keine Ausnahme, sondern der eingebaute Zustand: mit
 * diesen Werten zeigt die Sprachkarte „Tippen geht immer" und bietet die
 * Sprachknöpfe nicht an - ohne Fehlermeldung und ohne Dialog.
 */
const LEERE_FAEHIGKEITEN: SpeechCapabilitiesPayload = {
  text_mode_available: true,
  text_mode_note: t("Der Lernraum ist gerade nicht erreichbar. Alles hier laeuft ohne Modell."),
  speech_to_text: { available: false, reason: t("Der Lernraum ist gerade nicht erreichbar."), model_id: null, label: "", language: null, language_label: "" },
  text_to_speech: { available: false, reason: t("Der Lernraum ist gerade nicht erreichbar."), model_id: null, label: "", language: null, language_label: "" },
  input: { max_bytes: 0, max_seconds: 0, accepted_types: [], sample_rate: 16000, needs_transcoding: true, transcoding_note: "" },
  runtime: {
    transcriber_reachable: false,
    transcriber_note: "",
    synthesizer_available: false,
    synthesizer_note: "",
    ffmpeg_present: false,
    ffmpeg_note: "",
  },
  default_voice_id: null,
  recommended_voice_id: null,
  installed_voice_ids: [],
};

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export interface ApiRequestOptions extends Omit<RequestInit, "body"> {
  body?: BodyInit | null;
}

function endpoint(path: string) {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  return `${API_PREFIX}${normalizedPath}`;
}

/**
 * Die Meldung einer Fehlerantwort. Das Backend schreibt seine Meldungen auf
 * Deutsch; feste Saetze stehen deshalb auch im Woerterbuch und kommen in der
 * Oberflaechensprache an. Saetze mit eingesetzten Werten bleiben deutsch.
 */
function responseMessage(payload: unknown, fallback: string) {
  if (typeof payload === "object" && payload !== null && "detail" in payload && typeof payload.detail === "string") {
    return translateServerText(payload.detail);
  }
  if (typeof payload === "object" && payload !== null && "message" in payload && typeof payload.message === "string") {
    return translateServerText(payload.message);
  }
  return fallback;
}

/** `Accept-Language` mit der Sprache der Oberflaeche, nicht der des Browsers: danach antwortet der Assistent. */
function withLanguage(headers: Headers) {
  headers.set("Accept-Language", getLocale());
  return headers;
}

export function canUseDemoFallback(error: unknown) {
  return isDemoFallbackEnabled() && error instanceof ApiError && (error.status === 0 || error.status === 503);
}

export async function apiRequest(path: string, options: ApiRequestOptions = {}): Promise<unknown> {
  if (typeof window === "undefined") {
    throw new ApiError(t("API-Anfragen sind nur im Browser verfügbar."), 0);
  }

  const { body, headers: suppliedHeaders, ...fetchOptions } = options;
  const headers = withLanguage(new Headers(suppliedHeaders));
  if (!headers.has("Accept")) {
    headers.set("Accept", "application/json");
  }
  // `FormData` darf **keinen** Content-Type bekommen: der Browser haengt die
  // Multipart-Boundary selbst an. Ein gesetzter `application/json` wuerde den
  // Upload fuer das Backend unlesbar machen, ohne dass die Antwort den Grund
  // verraet.
  const isMultipart = typeof FormData !== "undefined" && body instanceof FormData;
  if (body && !isMultipart && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  let response: Response;
  try {
    response = await fetch(endpoint(path), {
      ...fetchOptions,
      body,
      headers,
      cache: fetchOptions.cache ?? "no-store",
      credentials: fetchOptions.credentials ?? "include",
      redirect: fetchOptions.redirect ?? "manual",
    });
  } catch {
    throw new ApiError(t("Die Verbindung zum Lernraum konnte nicht hergestellt werden."), 0);
  }

  const payload = response.status === 204 ? undefined : await response.json().catch(() => undefined);

  if (!response.ok) {
    throw new ApiError(responseMessage(payload, t("Die Anfrage ist fehlgeschlagen ({status}).", { status: response.status })), response.status);
  }

  return payload;
}

export async function getSession() {
  try {
    const payload = parseUserSession(await apiRequest("/v1/auth/me"));
    if (payload === null) {
      return null;
    }
    if ("authenticated" in payload && payload.authenticated === false) {
      return null;
    }
    if ("user" in payload) {
      return parseUserResponse(payload.user);
    }
    return parseUserResponse(payload);
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      return null;
    }
    if (canUseDemoFallback(error)) {
      return { id: "demo-user", username: t("Demo-Lernende"), email: null };
    }
    throw error;
  }
}

export async function getDashboard(): Promise<DashboardData> {
  try {
    return normalizeDashboard(await apiRequest("/v1/dashboard"));
  } catch (error) {
    if (canUseDemoFallback(error)) {
      return normalizeDashboard(getDemoDashboard());
    }
    throw error;
  }
}

/** Stunden eines Zeitraums. ``start`` und ``end`` sind lokale ``YYYY-MM-DD``-Tage, beide einschließlich. */
export async function getScheduleRange(start: string, end: string): Promise<ScheduleRange> {
  const query = `?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`;
  try {
    return normalizeScheduleRange(await apiRequest(`/v1/lessons${query}`));
  } catch (error) {
    if (canUseDemoFallback(error)) {
      return normalizeScheduleRange(getDemoScheduleRange(start, end));
    }
    throw error;
  }
}

/**
 * Der Willkommenssatz des Tages.
 *
 * Bewusst ohne Demo-Fallback: ein erfundener Begrüßungssatz wäre nicht als solcher
 * erkennbar, und die Kachel soll nicht so tun, als stünde eine KI dahinter.
 */
export async function getGreeting(): Promise<Greeting> {
  return normalizeGreeting(await apiRequest("/v1/greeting"));
}

/** Erzeugt den Satz neu. Kosten einen Modellaufruf, deshalb nur bewusst aufrufen. */
export async function refreshGreeting(): Promise<Greeting> {
  return normalizeGreeting(await apiRequest("/v1/greeting/refresh", { method: "POST" }));
}

function studyTaskPath(taskId: ApiIdentifier, action: "start" | "stop" | "complete") {
  if (!String(taskId).trim()) {
    throw new ApiError(t("Die Lernaufgabe ist ungültig."), 422);
  }
  if (isDemoTaskId(taskId)) {
    throw new ApiError(t("Demo-Aufgaben können nicht als echte Lernsessions gestartet werden."), 422);
  }
  return `/v1/study/tasks/${encodeURIComponent(String(taskId))}/${action}`;
}

async function studyAction(taskId: ApiIdentifier, action: "start" | "stop" | "complete") {
  const payload = await apiRequest(studyTaskPath(taskId, action), {
    method: "POST",
    body: JSON.stringify({}),
  });
  return normalizeStudyAction(payload, taskId);
}

export interface IntegrationWrite {
  kind: string;
  name: string;
  config: Record<string, string>;
  credentials?: Record<string, string>;
  enabled?: boolean;
}

export interface IntegrationChanges {
  name?: string;
  config?: Record<string, string>;
  credentials?: Record<string, string>;
  enabled?: boolean;
}

export async function getIntegrations(): Promise<IntegrationRead[]> {
  const payload = await apiRequest('/v1/integrations');
  return parseIntegrationArray(payload);
}

export async function getIntegrationCatalog(): Promise<IntegrationCatalog[]> {
  return parseIntegrationCatalogArray(await apiRequest("/v1/integrations/catalog"));
}

export async function createIntegration(input: IntegrationWrite): Promise<IntegrationRead> {
  const payload = await apiRequest("/v1/integrations", {
    method: "POST",
    body: JSON.stringify(input),
  });
  return parseIntegrationRead(payload);
}

export async function getIntegration(integrationId: number): Promise<IntegrationRead> {
  return parseIntegrationRead(await apiRequest(resourcePath("integrations", integrationId)));
}

export async function updateIntegration(integrationId: number, changes: IntegrationChanges): Promise<IntegrationRead> {
  const payload = await apiRequest(resourcePath("integrations", integrationId), {
    method: "PATCH",
    body: JSON.stringify(changes),
  });
  return parseIntegrationRead(payload);
}

export async function deleteIntegration(integrationId: number): Promise<void> {
  await apiRequest(resourcePath("integrations", integrationId), { method: "DELETE" });
}

export async function testIntegration(integrationId: number): Promise<IntegrationTestResult> {
  const payload = await apiRequest(resourcePath("integrations", integrationId, "test"), {
    method: "POST",
    body: JSON.stringify({}),
  });
  return parseIntegrationTestResult(payload);
}

export async function syncIntegration(integrationId: number): Promise<IntegrationSyncResult> {
  const payload = await apiRequest(resourcePath("integrations", integrationId, "sync"), {
    method: "POST",
    body: JSON.stringify({}),
  });
  return parseIntegrationSyncResult(payload);
}

/**
 * Startet die Einwilligung von Google.
 *
 * **Leitet nicht selbst weiter.** Die Oberfläche muss vorher noch die Freigabe
 * zum Schreiben anzeigen können, und das ist nur hier möglich. Danach geht der
 * Browser von Hand an `authorization_url` - dort zeigt Google an, welche
 * Rechte verlangt werden.
 */
export async function authorizeIntegration(integrationId: number): Promise<IntegrationAuthorize> {
  const payload = await apiRequest(resourcePath("integrations", integrationId, "authorize"), {
    method: "POST",
    body: JSON.stringify({}),
  });
  return parseIntegrationAuthorize(payload);
}

/** Ob der Lernraum Termine in verbundene Kalender schreiben darf. Standard ist aus. */
export async function getCalendarWriting(): Promise<CalendarWriting> {
  return parseCalendarWriting(await apiRequest("/v1/calendar/writing"));
}

/**
 * Die Freigabe erteilen oder zurücknehmen.
 *
 * `enabled` ist ein Pflichtfeld ohne Default: ein fehlendes `undefined` würde
 * hier stillschweigend zurückschalten, und ein Default `false` wäre ein
 * Schalter, der sich selbst zurücksetzt.
 */
export async function setCalendarWriting(enabled: boolean): Promise<CalendarWriting> {
  return parseCalendarWriting(
    await apiRequest("/v1/calendar/writing", { method: "PUT", body: JSON.stringify({ enabled }) }),
  );
}

/** Die abonnierbare ICS-Adresse, falls eingerichtet. */
export async function getCalendarFeed(): Promise<CalendarFeed> {
  return parseCalendarFeed(await apiRequest("/v1/calendar/feed"));
}

/** Erzeugt einen neuen Abo-Schlüssel. Der alte ist danach ungültig. */
export async function createCalendarFeed(): Promise<CalendarFeed> {
  return parseCalendarFeed(await apiRequest("/v1/calendar/feed", { method: "POST" }));
}

export async function getDocuments(): Promise<DocumentRecord[]> {
  const payload = await apiRequest("/v1/documents");
  return parseDocumentArray(payload);
}

export interface DocumentMaterialInput {
  material_kind?: MaterialKind;
  material_topic?: string | null;
  material_date?: string | null;
  subject_id?: number | null;
}

/** Korrigiert die Erkennung; von Hand Gesetztes ueberschreibt das Backend danach nie mehr. */
export async function updateDocumentMaterial(documentId: number, input: DocumentMaterialInput): Promise<DocumentRecord> {
  const payload = await apiRequest(`/v1/documents/${encodeURIComponent(String(documentId))}/material`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
  return parseDocumentArray([payload])[0];
}

export interface DocumentSearchInput {
  query: string;
  limit?: number;
}

export interface DocumentAskInput {
  question: string;
  limit?: number;
}

export async function searchDocuments(input: DocumentSearchInput): Promise<DocumentSearchResult> {
  const payload = await apiRequest("/v1/documents/search", {
    method: "POST",
    body: JSON.stringify({ query: input.query, limit: input.limit }),
  });
  return parseDocumentSearchResult(payload);
}

export async function askDocuments(input: DocumentAskInput): Promise<DocumentAnswer> {
  const payload = await apiRequest("/v1/documents/ask", {
    method: "POST",
    body: JSON.stringify({ question: input.question, limit: input.limit }),
  });
  return parseDocumentAnswer(payload);
}

export function startStudyTask(taskId: ApiIdentifier) {
  return studyAction(taskId, "start");
}

export function stopStudyTask(taskId: ApiIdentifier) {
  return studyAction(taskId, "stop");
}

export function completeStudyTask(taskId: ApiIdentifier) {
  return studyAction(taskId, "complete");
}

/*
 * Ab hier die schreibenden Endpunkte.
 *
 * Zwei Regeln gelten fuer alle neuen Aufrufe:
 *
 * 1. Ids werden per `encodeURIComponent` in den Pfad gesetzt. `studyTaskPath`
 *    tat das schon, die Integrationsaufrufe taten es nicht - bei ganzzahligen
 *    Ids folgenlos, aber inkonsistent.
 * 2. Ein `PATCH` schickt nur gesetzte Felder. Die `*Update`-Schemata des
 *    Backends lehnen `null` bei fast allen Feldern ab, und ein fehlendes Feld
 *    bedeutet dort "unveraendert" - ein explizites `null` waere ein 422.
 */

/** Pfad einer Sammlung mit optionalen Query-Parametern, die `undefined` nie erreichen. */
function collectionPath(path: string, query: Record<string, string | number | boolean | undefined>) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== "") {
      search.set(key, String(value));
    }
  }
  const suffix = search.toString();
  return suffix ? `${path}?${suffix}` : path;
}

/**
 * Baut `/v1/{collection}/{id}` und haengt beliebig viele Pfadabschnitte an.
 *
 * Die Abschnitte sind variabel, weil nicht jede Aktion eine eigene Route
 * hat: `study/tasks/{id}/start` hat einen, `study/tasks/{id}/dependencies/{dep}`
 * zwei. Jeder Abschnitt wird einzeln kodiert, sonst bricht eine Id mit
 * Sonderzeichen den Pfad.
 */
function resourcePath(collection: string, id: number, ...segments: string[]) {
  const base = `/v1/${collection}/${encodeURIComponent(String(id))}`;
  return segments.length > 0 ? `${base}/${segments.map(encodeURIComponent).join("/")}` : base;
}

/** Wirft, statt eine kaputte ID als Anfrage zu schicken. */
function assertId(id: number, label: string) {
  if (!Number.isInteger(id) || id <= 0) {
    throw new ApiError(t("{label} ist ungültig.", { label }), 422);
  }
}

/**
 * Entfernt `undefined`, damit PATCH-Bodies keine leeren Felder tragen.
 *
 * Das generische `T` bleibt erhalten, damit der Rueckgabewert weiter die
 * Schreibeingabe ist - `compact(changes)` liefert wieder `SubjectChanges` und
 * nicht `Record<string, unknown>`.
 */
function compact<T extends object>(payload: T): T {
  return Object.fromEntries(Object.entries(payload).filter(([, value]) => value !== undefined)) as T;
}

/* ---------------------------------------------------------------- Anmeldung */

export interface LoginCredentials {
  username: string;
  password: string;
}

export async function login(credentials: LoginCredentials) {
  const payload = await apiRequest("/v1/auth/login", {
    method: "POST",
    body: JSON.stringify(credentials),
  });
  return parseUserResponse(payload);
}

export async function logout() {
  await apiRequest("/v1/auth/logout", { method: "POST" });
}

/* ---------------------------------------------------------- Ersteinrichtung */

export interface SetupInput {
  name: string;
  timezone?: string;
}

/**
 * Ob dieser Lernraum noch eingerichtet werden muss.
 *
 * Die Einrichtung gibt es nur ohne Anmeldung (Desktop-App). Ein Server mit
 * Anmeldung antwortet mit 404, und ein nicht erreichbares Backend wirft - in
 * beiden Faellen ist die Antwort `false`: dann entscheidet nicht die
 * Einrichtung, sondern die Seite selbst, was sie zeigt.
 */
export async function getSetupStatus(): Promise<boolean> {
  try {
    const payload = await apiRequest("/v1/setup");
    return typeof payload === "object" && payload !== null && "needs_setup" in payload && payload.needs_setup === true;
  } catch {
    return false;
  }
}

export async function completeSetup(input: SetupInput) {
  const payload = await apiRequest("/v1/setup", {
    method: "POST",
    body: JSON.stringify(compact(input)),
  });
  return parseUserResponse(payload);
}

/* ------------------------------------------------------------------- Fächer */

export interface SubjectWrite {
  name: string;
  color?: string;
  teacher_id?: number | null;
  description?: string | null;
}

export interface SubjectChanges {
  name?: string;
  color?: string;
  teacher_id?: number | null;
  description?: string | null;
}

export async function getSubjects(): Promise<Subject[]> {
  return parseSubjectArray(await apiRequest("/v1/subjects"));
}

/** Nur die `{id, name}`-Paare, die ein Formular-Picker braucht. */
export async function getSubjectOptions(): Promise<SubjectOption[]> {
  return collectSubjectNames(await apiRequest("/v1/subjects"));
}

export async function createSubject(input: SubjectWrite): Promise<Subject> {
  const payload = await apiRequest("/v1/subjects", {
    method: "POST",
    body: JSON.stringify(compact({ ...input })),
  });
  return parseSubjectRead(payload);
}

export async function getSubject(subjectId: number): Promise<Subject> {
  assertId(subjectId, t("Das Fach"));
  return parseSubjectRead(await apiRequest(resourcePath("subjects", subjectId)));
}

export async function updateSubject(subjectId: number, changes: SubjectChanges): Promise<Subject> {
  assertId(subjectId, t("Das Fach"));
  const payload = await apiRequest(resourcePath("subjects", subjectId), {
    method: "PATCH",
    body: JSON.stringify(compact(changes)),
  });
  return parseSubjectRead(payload);
}

export async function deleteSubject(subjectId: number): Promise<void> {
  assertId(subjectId, t("Das Fach"));
  await apiRequest(resourcePath("subjects", subjectId), { method: "DELETE" });
}

/* ------------------------------------------------------------- Hausaufgaben */

export interface HomeworkWrite {
  title: string;
  due_date: string;
  subject_id?: number | null;
  description?: string | null;
  completed?: boolean;
}

export interface HomeworkChanges {
  title?: string;
  due_date?: string;
  subject_id?: number | null;
  description?: string | null;
}

export async function getHomework(completed?: boolean): Promise<Homework[]> {
  return parseHomeworkArray(await apiRequest(collectionPath("/v1/homework", { completed })));
}

export async function createHomework(input: HomeworkWrite): Promise<Homework> {
  const payload = await apiRequest("/v1/homework", { method: "POST", body: JSON.stringify(compact(input)) });
  return parseHomeworkRead(payload);
}

export async function getHomeworkItem(homeworkId: number): Promise<Homework> {
  assertId(homeworkId, t("Die Hausaufgabe"));
  return parseHomeworkRead(await apiRequest(resourcePath("homework", homeworkId)));
}

export async function updateHomework(homeworkId: number, changes: HomeworkChanges): Promise<Homework> {
  assertId(homeworkId, t("Die Hausaufgabe"));
  const payload = await apiRequest(resourcePath("homework", homeworkId), {
    method: "PATCH",
    body: JSON.stringify(compact(changes)),
  });
  return parseHomeworkRead(payload);
}

/**
 * Setzt den Erledigt-Status ausdruecklich.
 *
 * Das Backend kennt drei Routen dafuer: `POST .../complete`, `POST .../toggle`
 * und `PATCH .../completed`. Alle drei sind dieselbe Funktion, und die beiden
 * `POST`-Varianten **schalten um**, wenn kein Body kommt. Ein Haken im
 * Frontend, der "fertig" anzeigt, wuerde beim naechsten Klick wieder
 * aufmachen - deshalb wird hier bewusst die `PATCH`-Variante mit gesetztem
 * Wert benutzt.
 */
export async function setHomeworkCompleted(homeworkId: number, completed: boolean): Promise<Homework> {
  assertId(homeworkId, t("Die Hausaufgabe"));
  const payload = await apiRequest(resourcePath("homework", homeworkId, "completed"), {
    method: "PATCH",
    body: JSON.stringify({ completed }),
  });
  return parseHomeworkRead(payload);
}

export async function deleteHomework(homeworkId: number): Promise<void> {
  assertId(homeworkId, t("Die Hausaufgabe"));
  await apiRequest(resourcePath("homework", homeworkId), { method: "DELETE" });
}

/* ---------------------------------------------------------------- Prüfungen */

export interface ExamWrite {
  title: string;
  date: string;
  subject_id?: number | null;
  description?: string | null;
}

export interface ExamChanges {
  title?: string;
  date?: string;
  subject_id?: number | null;
  description?: string | null;
}

export async function getExams(upcoming = true): Promise<Exam[]> {
  return parseExamArray(await apiRequest(collectionPath("/v1/exams", { upcoming })));
}

export async function createExam(input: ExamWrite): Promise<Exam> {
  const payload = await apiRequest("/v1/exams", { method: "POST", body: JSON.stringify(compact(input)) });
  return parseExamRead(payload);
}

export async function getExam(examId: number): Promise<Exam> {
  assertId(examId, t("Die Prüfung"));
  return parseExamRead(await apiRequest(resourcePath("exams", examId)));
}

export async function updateExam(examId: number, changes: ExamChanges): Promise<Exam> {
  assertId(examId, t("Die Prüfung"));
  const payload = await apiRequest(resourcePath("exams", examId), {
    method: "PATCH",
    body: JSON.stringify(compact(changes)),
  });
  return parseExamRead(payload);
}

export async function deleteExam(examId: number): Promise<void> {
  assertId(examId, t("Die Prüfung"));
  await apiRequest(resourcePath("exams", examId), { method: "DELETE" });
}

/* ----------------------------------------------------------------- Kalender */

export interface CalendarEventWrite {
  title: string;
  start_time: string;
  end_time: string;
  description?: string | null;
  all_day?: boolean;
}

export interface CalendarEventChanges {
  title?: string;
  start_time?: string;
  end_time?: string;
  description?: string | null;
  all_day?: boolean;
}

/**
 * Die Sammlungsrouten sind doppelt gemountet (`/calendar` und
 * `/calendar/events`), die Punkte aber nur unter `/calendar/events/{id}`.
 * Deshalb durchgaengig die ausfuehrliche Variante.
 */
export async function getCalendarEvents(upcoming = true): Promise<CalendarEvent[]> {
  return parseCalendarEventArray(await apiRequest(collectionPath("/v1/calendar/events", { upcoming })));
}

export async function createCalendarEvent(input: CalendarEventWrite): Promise<CalendarEvent> {
  const payload = await apiRequest("/v1/calendar/events", { method: "POST", body: JSON.stringify(compact(input)) });
  return parseCalendarEventRead(payload);
}

export async function getCalendarEvent(eventId: number): Promise<CalendarEvent> {
  assertId(eventId, t("Der Kalendereintrag"));
  return parseCalendarEventRead(await apiRequest(resourcePath("calendar/events", eventId)));
}

export async function updateCalendarEvent(eventId: number, changes: CalendarEventChanges): Promise<CalendarEvent> {
  assertId(eventId, t("Der Kalendereintrag"));
  const payload = await apiRequest(resourcePath("calendar/events", eventId), {
    method: "PATCH",
    body: JSON.stringify(compact(changes)),
  });
  return parseCalendarEventRead(payload);
}

export async function deleteCalendarEvent(eventId: number): Promise<void> {
  assertId(eventId, t("Der Kalendereintrag"));
  await apiRequest(resourcePath("calendar/events", eventId), { method: "DELETE" });
}

/* ------------------------------------------------------------- Lernaufgaben */

export interface StudyTaskWrite {
  study_plan_id: number;
  title: string;
  duration_minutes?: number;
  topic_id?: number | null;
  description?: string | null;
  scheduled_for?: string | null;
  due_date?: string | null;
  priority?: number;
  order_index?: number;
}

export interface StudyTaskChanges {
  title?: string;
  duration_minutes?: number;
  topic_id?: number | null;
  description?: string | null;
  scheduled_for?: string | null;
  due_date?: string | null;
  priority?: number;
  status?: StudyTaskState;
  order_index?: number;
}

/**
 * Die Aufgaben eines Nutzers.
 *
 * `order` steuert die Sortierung. Das Backend kennt `priority` (die
 * Voreinstellung, weil genau das der Grund fuer die Spalte ist), `due` und
 * `position`. Ohne Demo-Fallback: eine erfundene Aufgabenliste waere an
 * ihren Lerninhalten nicht als solche erkennbar.
 */
export async function getStudyTasks(
  filter: { status?: StudyTaskState; studyPlanId?: number; order?: StudyTaskOrder } = {},
): Promise<StudyTask[]> {
  const payload = await apiRequest(
    collectionPath("/v1/study/tasks", {
      status: filter.status,
      study_plan_id: filter.studyPlanId,
      order: filter.order,
    }),
  );
  return parseStudyTaskArray(payload);
}

export async function createStudyTask(input: StudyTaskWrite): Promise<StudyTask> {
  const payload = await apiRequest("/v1/study/tasks", { method: "POST", body: JSON.stringify(compact(input)) });
  return parseStudyTaskRead(payload);
}

export async function getStudyTask(taskId: number): Promise<StudyTask> {
  assertId(taskId, t("Die Lernaufgabe"));
  return parseStudyTaskRead(await apiRequest(resourcePath("study/tasks", taskId)));
}

export async function updateStudyTask(taskId: number, changes: StudyTaskChanges): Promise<StudyTask> {
  assertId(taskId, t("Die Lernaufgabe"));
  const payload = await apiRequest(resourcePath("study/tasks", taskId), {
    method: "PATCH",
    body: JSON.stringify(compact(changes)),
  });
  return parseStudyTaskRead(payload);
}

export async function deleteStudyTask(taskId: number): Promise<void> {
  assertId(taskId, t("Die Lernaufgabe"));
  await apiRequest(resourcePath("study/tasks", taskId), { method: "DELETE" });
}

/** `POST .../start` antwortet als einzige Aktion mit einer Session statt einer Aufgabe. */
export async function startStudyTaskSession(taskId: number): Promise<StudySessionRecord> {
  assertId(taskId, t("Die Lernaufgabe"));
  const payload = await apiRequest(resourcePath("study/tasks", taskId, "start"), {
    method: "POST",
    body: JSON.stringify({}),
  });
  return parseStudySessionRead(payload);
}

/* -------------------------------------------------------------- Lernpläne */

export async function getStudyPlans(): Promise<StudyPlan[]> {
  return parseStudyPlanArray(await apiRequest("/v1/study/plans"));
}

export async function createStudyPlan(input: { title: string; description?: string | null }): Promise<StudyPlan> {
  const payload = await apiRequest("/v1/study/plans", {
    method: "POST",
    body: JSON.stringify(compact({ title: input.title, description: input.description ?? null })),
  });
  return parseStudyPlanRead(payload);
}

export async function renameStudyPlan(planId: number, title: string): Promise<StudyPlan> {
  assertId(planId, t("Der Lernplan"));
  const payload = await apiRequest(resourcePath("study/plans", planId), {
    method: "PATCH",
    body: JSON.stringify({ title }),
  });
  return parseStudyPlanRead(payload);
}

/**
 * Löscht den Plan mitsamt Aufgaben, Serien und Lernsessions.
 *
 * Auch die Abhängigkeiten fallen mit: eine Abhängigkeit auf eine gelöschte
 * Aufgabe könnte nie erfüllt werden, also wäre sie schlimmer als keine.
 */
export async function deleteStudyPlan(planId: number): Promise<void> {
  assertId(planId, t("Der Lernplan"));
  await apiRequest(resourcePath("study/plans", planId), { method: "DELETE" });
}

/* ------------------------------------------------------- Serien & Abhängigkeiten */

export async function getStudySeries(studyPlanId?: number): Promise<StudyTaskSeries[]> {
  const payload = await apiRequest(collectionPath("/v1/study/series", { study_plan_id: studyPlanId }));
  return parseStudySeriesArray(payload);
}

export async function createStudySeries(input: StudyTaskSeriesInput): Promise<StudyTaskSeries> {
  const payload = await apiRequest("/v1/study/series", {
    method: "POST",
    body: JSON.stringify(compact(input)),
  });
  return parseStudySeriesRead(payload);
}

/**
 * Aendert die **Vorlage** einer Serie, nicht die bereits erzeugten Aufgaben.
 *
 * Wirkt also erst die naechste Occurrence. Eine Serie, deren Vergangenheit sich
 * nachtraeglich umschreiben laesst, waere keine Serie mehr, sondern eine
 * nachtraeglich umgeschriebene Liste.
 */
export async function updateStudySeries(
  seriesId: number,
  changes: Partial<Pick<StudyTaskSeriesInput, "title" | "description" | "duration_minutes" | "priority" | "until" | "occurrence_limit">>,
): Promise<StudyTaskSeries> {
  assertId(seriesId, t("Die Serie"));
  const payload = await apiRequest(resourcePath("study/series", seriesId), {
    method: "PATCH",
    body: JSON.stringify(compact(changes)),
  });
  return parseStudySeriesRead(payload);
}

/** Beendet die Serie und storniert ihre offenen Aufgaben. Erledigte bleiben stehen. */
export async function cancelStudySeries(seriesId: number): Promise<void> {
  assertId(seriesId, t("Die Serie"));
  await apiRequest(resourcePath("study/series", seriesId), { method: "DELETE" });
}

export async function getStudyDependencies(taskId: number): Promise<StudyTaskDependency[]> {
  assertId(taskId, t("Die Lernaufgabe"));
  return parseStudyDependencyArray(await apiRequest(resourcePath("study/tasks", taskId, "dependencies")));
}

/**
 * Macht `dependsOnTaskId` zur Voraussetzung von `taskId`.
 *
 * Ein Zyklus kommt als 422 vom Backend, nicht als Fehler hier - der Aufrufer
 * zeigt dann die Meldung an, statt sie zu erfinden.
 */
export async function addStudyDependency(taskId: number, dependsOnTaskId: number): Promise<StudyTaskDependency> {
  assertId(taskId, t("Die Lernaufgabe"));
  assertId(dependsOnTaskId, t("Die Voraussetzung"));
  const payload = await apiRequest(resourcePath("study/tasks", taskId, "dependencies"), {
    method: "POST",
    body: JSON.stringify({ depends_on_task_id: dependsOnTaskId }),
  });
  return parseStudyDependencyRead(payload);
}

export async function removeStudyDependency(taskId: number, dependsOnTaskId: number): Promise<void> {
  assertId(taskId, t("Die Lernaufgabe"));
  assertId(dependsOnTaskId, t("Die Voraussetzung"));
  await apiRequest(resourcePath("study/tasks", taskId, "dependencies", String(dependsOnTaskId)), { method: "DELETE" });
}

/**
 * Erzeugt aus den Klausurthemen Aufgaben in einem Plan.
 *
 * Ein fachlich fehlgeschlagener Lauf kommt als 422 mit deutscher Meldung
 * (etwa "keine Themen hinterlegt"), nicht als Verbindungsfehler - daran
 * unterscheidet der Aufrufer, ob er den Nutzer informieren muss.
 */
export async function generateExamTasks(
  examId: number,
  input: {
    study_plan_id: number;
    duration_minutes?: number;
    lead_days?: number;
    scheduled_hour?: number;
    only_missing?: boolean;
    chain_dependencies?: boolean;
  },
): Promise<ExamTaskPlanResult> {
  assertId(examId, t("Die Klausur"));
  const payload = await apiRequest(resourcePath("exams", examId, "tasks"), {
    method: "POST",
    body: JSON.stringify(compact(input)),
  });
  return parseExamTaskPlanResult(payload);
}

/* -------------------------------------------------------- Lernfortschritt */

export interface TopicProgressWrite {
  /** 0 bis 1. Die fuenf Stufen holt sich die Oberflaeche aus `TopicProgress`. */
  confidence: number;
  /**
   * Weglassen heisst "jetzt" (das Backend traegt `utc_now()` ein), `null`
   * loescht den Anker. `compact` behält ein `null` - nur `undefined` faellt
   * weg, und genau das ist hier der Unterschied zwischen "leeren" und
   * "unveraendert lassen".
   */
  last_reviewed_at?: string | null;
  notes?: string | null;
}

/**
 * Das Lernprotokoll als Auswertung: Serie, Wochenziel, Lernzeit je Fach und
 * Verteilung auf die Wochentage.
 *
 * Ohne Demo-Fallback, wie bei den anderen Lerndaten: eine erfundene Serie waere
 * an ihren Zahlen nicht als solche erkennbar, und eine erfundene Lernzeit
 * schon gar nicht.
 */
export async function getLearningInsights(): Promise<LearningInsights> {
  return parseLearningInsights(await apiRequest("/v1/progress/insights"));
}

/**
 * Der Lernstand aller Themen, optional auf ein Fach eingeschraenkt.
 *
 * `subjectId` wird von der Fortschrittskarte nicht gebraucht - sie zeigt alle
 * Themen. Er ist hier, weil der Endpunkt ihn anbietet und eine zweite
 * Filterstelle in der Oberflaeche es waere.
 */
export async function getTopicProgress(subjectId?: number): Promise<TopicProgress[]> {
  return parseTopicProgressArray(await apiRequest(collectionPath("/v1/progress/topics", { subject_id: subjectId })));
}

/**
 * Konfidenz und Wiederholungsdatum eines Themas schreiben.
 *
 * `PUT` und nicht `PATCH`, weil der Aufruf die Zeile anlegt, falls sie fehlt.
 * Das Backend nennt Bezeichnung und Intervall zurueck - die Oberflaeche
 * uebernimmt die Antwort, statt sie selbst zu berechnen.
 */
export async function saveTopicProgress(topicId: number, input: TopicProgressWrite): Promise<TopicProgress> {
  assertId(topicId, t("Das Thema"));
  const payload = await apiRequest(resourcePath("progress/topics", topicId), {
    method: "PUT",
    body: JSON.stringify(compact(input)),
  });
  return parseTopicProgressRead(payload);
}

/** Das Wochenziel in gelernten Minuten. Antwort ist die Woche mit dem neuen Ziel. */
export async function setWeeklyGoal(goalMinutes: number): Promise<WeeklyGoal> {
  const payload = await apiRequest("/v1/progress/goal", {
    method: "PATCH",
    body: JSON.stringify({ goal_minutes: goalMinutes }),
  });
  return parseWeeklyGoalRead(payload);
}

/* ------------------------------------------------------------- Belohnungen */

/**
 * Punktekonto, Auszeichnungen und die letzten Buchungen - in **einer** Anfrage.
 *
 * Die drei Teile gehoeren zu derselben Frage und werden immer zusammen gelesen.
 * Drei Endpunkte wuerden drei Anfragen und drei Moeglichkeiten schaffen, in denen
 * die Karte einen halben Stand zeigt: eine Punktzahl ohne die Buchung, aus der sie
 * stammt. Ausserdem kommt die Regel mit, damit die Karte sie nicht nachbaut.
 *
 * Ohne Demo-Fallback, wie bei den Lerndaten: eine erfundene Punktzahl waere an
 * sich nicht als solche erkennbar - sie wuerde einfach dastehen.
 *
 * `limit` begrenzt die **Liste** der Buchungen, nicht die Summe. Das Backend
 * begrenzt den Wert von sich aus nach oben.
 */
export async function getRewards(limit?: number): Promise<RewardOverview> {
  return parseRewardOverview(await apiRequest(collectionPath("/v1/rewards", { limit })));
}

/* ------------------------------------------------------------------- Noten */

export interface GradeWrite {
  subject_id: number;
  grade_value: number;
  kind?: GradeKind;
  weight?: number;
  graded_on: string;
  comment?: string | null;
}

export interface GradeChanges {
  subject_id?: number | null;
  grade_value?: number;
  kind?: GradeKind;
  weight?: number;
  graded_on?: string;
  comment?: string | null;
}

/**
 * Die Noten des Nutzers, neueste zuerst.
 *
 * Ohne Demo-Fallback: eine erfundene Notenliste waere an ihren Zahlen nicht als
 * solche erkennbar, und ein Schnitt aus erfundenen Noten ist eine erfundene
 * Aussage ueber die eigene Leistung.
 */
export async function getGrades(filter: { subjectId?: number; limit?: number } = {}): Promise<Grade[]> {
  return parseGradeArray(
    await apiRequest(collectionPath("/v1/grades", { subject_id: filter.subjectId, limit: filter.limit })),
  );
}

export async function createGrade(input: GradeWrite): Promise<Grade> {
  assertId(input.subject_id, t("Das Fach"));
  const payload = await apiRequest("/v1/grades", { method: "POST", body: JSON.stringify(compact(input)) });
  return parseGradeRead(payload);
}

export async function updateGrade(gradeId: number, changes: GradeChanges): Promise<Grade> {
  assertId(gradeId, t("Die Note"));
  const payload = await apiRequest(resourcePath("grades", gradeId), {
    method: "PATCH",
    body: JSON.stringify(compact(changes)),
  });
  return parseGradeRead(payload);
}

export async function deleteGrade(gradeId: number): Promise<void> {
  assertId(gradeId, t("Die Note"));
  await apiRequest(resourcePath("grades", gradeId), { method: "DELETE" });
}

/**
 * Schnitt, Verlauf und Hochrechnung in einem Aufruf.
 *
 * `projectedWeight` ist die Gewichtung, die noch aussteht - eine Angabe der
 * Lernenden, keine gespeicherte Groesse. Ohne sie bleibt die Hochrechnung leer,
 * und die Karte zeigt den Rueckblick.
 */
export async function getGradeSummary(filter: { subjectId?: number; projectedWeight?: number } = {}): Promise<GradeSummary> {
  return parseGradeSummary(
    await apiRequest(
      collectionPath("/v1/grades/summary", { subject_id: filter.subjectId, projected_weight: filter.projectedWeight }),
    ),
  );
}

/** Ob Noten den KI-Dienst erreichen duerfen. `false` ist der Normalfall. */
export async function getGradeSharing(): Promise<GradeSharing> {
  return parseGradeSharing(await apiRequest("/v1/grades/sharing"));
}

/** Die Zustimmung setzen oder zuruecknehmen. */
export async function setGradeSharing(enabled: boolean): Promise<GradeSharing> {
  return parseGradeSharing(
    await apiRequest("/v1/grades/sharing", { method: "PUT", body: JSON.stringify({ enabled }) }),
  );
}

/**
 * Die Fundstellen eines Dokuments, **ohne** etwas anzulegen.
 *
 * `subjectId` ist das Vorgabefach fuer Zeilen, in denen kein Fachname steht.
 */
export async function previewGradeImport(input: {
  documentId: number;
  subjectId?: number;
  defaultKind?: GradeKind;
  defaultWeight?: number;
  defaultDate?: string;
}): Promise<GradeImportPreview> {
  assertId(input.documentId, t("Das Dokument"));
  return parseGradeImportPreview(
    await apiRequest(
      collectionPath("/v1/grades/import", {
        document_id: input.documentId,
        subject_id: input.subjectId,
        default_kind: input.defaultKind,
        default_weight: input.defaultWeight,
        default_date: input.defaultDate,
      }),
    ),
  );
}

/**
 * Die bestaetigten Zeilen als Noten anlegen.
 *
 * Nur die Schluessel: der Server liest Note, Art, Datum und Fach selbst aus dem
 * Dokument, damit die Oberflaeche keine Note erfinden kann, die in keiner
 * Zeile steht.
 */
export async function importGrades(input: {
  documentId: number;
  keys: string[];
  subjectId?: number;
  defaultKind?: GradeKind;
  defaultWeight?: number;
  defaultDate?: string;
}): Promise<GradeImportResult> {
  assertId(input.documentId, t("Das Dokument"));
  const payload = await apiRequest("/v1/grades/import", {
    method: "POST",
    body: JSON.stringify(
      compact({
        document_id: input.documentId,
        keys: input.keys,
        subject_id: input.subjectId,
        default_kind: input.defaultKind,
        default_weight: input.defaultWeight,
        default_date: input.defaultDate,
      }),
    ),
  });
  return parseGradeImportResult(payload);
}

/* --------------------------------------------------------- Benachrichtigungen */

/**
 * Die Erinnerungseinstellungen: Hauptschalter, Kanäle, Uhrzeit, Ruhezeiten.
 *
 * Ohne Demo-Fallback: eine erfundene Tageserinnerung, die sich nie ausgeht, wäre
 * eine Zusage, die das Backend nicht halten kann.
 */
export async function getNotificationSettings() {
  return parseNotificationSettings(await apiRequest("/v1/notifications/settings"));
}

export interface NotificationSettingsChanges {
  enabled?: boolean;
  channels?: Record<string, boolean>;
  reminder_time?: string;
  quiet_hours?: { enabled: boolean; start: string; end: string };
}

/**
 * Die Einstellungen aendern. Die Antwort ist die **gespeicherte** Lage.
 *
 * Gesendet wird nur, was auch wirklich geaendert werden soll: `compact` laesst
 * `undefined` fallen, und ein leeres Feld waere sonst ein 422, weil die
 * Schreibschemas `null` bei fast allen Feldern ablehnen.
 */
export async function updateNotificationSettings(changes: NotificationSettingsChanges) {
  return parseNotificationSettings(
    await apiRequest("/v1/notifications/settings", {
      method: "PATCH",
      body: JSON.stringify(compact(changes)),
    }),
  );
}

/** Der oeffentliche VAPID-Schluessel, falls der Server eines eingerichtet hat. */
export async function getVapidKey() {
  return parseVapidKey(await apiRequest("/v1/notifications/vapid"));
}

export async function getPushSubscriptions() {
  return parsePushSubscriptionArray(await apiRequest("/v1/notifications/subscriptions"));
}

/** Ein Endgeraet anmelden. Derselbe Endpunkt erneuert die Zeile statt eine neue anzulegen. */
export async function createPushSubscription(subscription: { endpoint: string; p256dh: string; auth: string }) {
  return parsePushSubscription(
    await apiRequest("/v1/notifications/subscriptions", {
      method: "POST",
      body: JSON.stringify(subscription),
    }),
  );
}

export async function deletePushSubscription(subscriptionId: number) {
  assertId(subscriptionId, t("Das Endgerät"));
  await apiRequest(resourcePath("notifications/subscriptions", subscriptionId), { method: "DELETE" });
}

/**
 * Die Erinnerungen, die noch niemand gesehen hat - der **lokale** Weg.
 *
 * `after` ist ein Id-Zeiger: die Seite merkt sich die groesste Id, die sie
 * schon abgeholt hat, und fragt danach. So bekommt sie jede genau einmal, auch
 * wenn der Dienst in der Nacht nichts Neues hatte.
 */
export async function getPendingNotifications(after?: number) {
  return parseNotificationArray(await apiRequest(collectionPath("/v1/notifications", { after })));
}

/** Als gelesen quittieren. Ohne Antwort: die Seite zeigt sie ja schon. */
export async function markNotificationsRead(ids: number[]): Promise<number> {
  return parseMarkReadResult(
    await apiRequest("/v1/notifications/read", { method: "POST", body: JSON.stringify({ ids }) }),
  );
}

/**
 * Einen Lauf jetzt ausloesen - der Knopf in den Einstellungen.
 *
 * Der einzige Weg, planmaessige Arbeit von aussen zu starten, und er geht
 * auch dann, wenn der planmaessige Thread aus ist.
 */
export async function runReminders() {
  return parseReminderRun(await apiRequest("/v1/notifications/reminders/run", { method: "POST" }));
}

/* ------------------------------------------------------------------- Dateien */

/**
 * Laedt eine Datei als `multipart/form-data` hoch.
 *
 * Das ist die einzige nicht-JSON-Route des Backends. Deshalb wird hier nicht
 * `apiRequest` mit einem JSON-Body benutzt, sondern `FormData` - und der
 * `Content-Type` bewusst **nicht** gesetzt, weil der Browser ihn inklusive
 * Boundary selbst setzen muss. Ein manuell gesetzter Header ohne Boundary
 * macht den Upload beim Backend unlesbar.
 */
export async function uploadFile(file: File): Promise<FileUploadResult> {
  const form = new FormData();
  form.append("file", file);
  const payload = await apiRequest("/v1/files", { method: "POST", body: form });
  return parseFileUploadResponse(payload);
}

/* ---------------------------------------------------------- Sprach-Assistent */

/**
 * Was der Sprach-Assistent gerade kann.
 *
 * Ohne Demo-Fallback, und zwar aus zwei Gruenden: Eine erfundene
 * Faehigkeitsangabe wuerde der Sprachausgabe eine Stimme andeuten, die es nicht
 * gibt - und der Textmodus soll genau dann erscheinen, wenn nichts geladen ist.
 * Der Endpunkt antwortet ohnehin immer mit 200; ein Fehler hier bedeutet
 * "Server nicht erreichbar", und dann ist Textmodus die richtige Antwort.
 */
export async function getSpeechCapabilities(): Promise<SpeechCapabilitiesPayload> {
  const payload = await apiRequest("/v1/speech/capabilities");
  return parseSpeechCapabilities(payload) ?? LEERE_FAEHIGKEITEN;
}

/** Die Piper-Stimmen mit ihrem Stand. */
export async function getSpeechVoices(): Promise<VoiceListPayload> {
  return parseVoiceList(await apiRequest("/v1/speech/voices")) ?? { voices: [], default_voice_id: null };
}

export async function downloadVoice(voiceId: string): Promise<void> {
  await apiRequest(`/v1/speech/voices/${encodeURIComponent(voiceId)}/download`, { method: "POST" });
}

export async function cancelVoiceDownload(voiceId: string): Promise<void> {
  await apiRequest(`/v1/speech/voices/${encodeURIComponent(voiceId)}/cancel`, { method: "POST" });
}

export async function deleteVoice(voiceId: string): Promise<void> {
  await apiRequest(`/v1/speech/voices/${encodeURIComponent(voiceId)}`, { method: "DELETE" });
}

/**
 * Schickt eine Aufnahme zum Transkribieren.
 *
 * `blob` ist der Inhalt aus `MediaRecorder`, `mime` der Typ, den der Browser
 * fuer genau diese Aufnahme benutzt hat - **nicht** ein selbst gewaehlter Wert.
 * Der Unterschied ist der ganze Formatunterschied zwischen Chrome (WebM/Opus)
 * und Safari (MP4/AAC), und der Server riecht den Container nicht.
 */
export async function transcribeAudio(blob: Blob, mime: string): Promise<{ text: string; model_id: string; transcoded: boolean; message: string }> {
  const form = new FormData();
  form.append("file", blob, `aufnahme${endungFuerFormat(mime)}`);
  const payload = await apiRequest("/v1/speech/transcribe", { method: "POST", body: form });
  const row = typeof payload === "object" && payload !== null ? (payload as Record<string, unknown>) : {};
  return {
    text: typeof row.text === "string" ? row.text : "",
    model_id: typeof row.model_id === "string" ? row.model_id : "unbekannt",
    transcoded: row.transcoded === true,
    message: typeof row.message === "string" ? row.message : "",
  };
}

/**
 * Holt eine WAV-Datei fuer die Sprachausgabe.
 *
 * Bewusst **nicht** ueber `apiRequest`: das dort erwartet JSON, und hier kommt
 * ein Ton. Als base64 im JSON waere die Datei um ein Drittel groesser - und
 * muesste im Browser wieder dekodiert werden, ohne dass etwas gewuenne. Der
 * Pfad laeuft trotzdem ueber denselben Proxy (`/api/backend/v1/...`), bleibt
 * also same-origin und passt unter `connect-src 'self'`.
 *
 * Ein Fehler wird zu `ApiError` mit der deutschen `detail`-Meldung des
 * Backends - sonst haette die Oberflaeche keine Ahnung, warum es keinen Ton
 * gab.
 */
export async function fetchSpeechAudio(text: string, voiceId?: string): Promise<Blob> {
  if (typeof window === "undefined") {
    throw new ApiError(t("API-Anfragen sind nur im Browser verfügbar."), 0);
  }
  let response: Response;
  try {
    response = await fetch(endpoint("/v1/speech/speak"), {
      method: "POST",
      headers: withLanguage(new Headers({ "Content-Type": "application/json", Accept: "audio/wav" })),
      body: JSON.stringify(voiceId ? { text, voice_id: voiceId } : { text }),
      cache: "no-store",
      credentials: "include",
      redirect: "manual",
    });
  } catch {
    throw new ApiError(t("Die Verbindung zum Lernraum konnte nicht hergestellt werden."), 0);
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => undefined);
    throw new ApiError(responseMessage(payload, t("Die Anfrage ist fehlgeschlagen ({status}).", { status: response.status })), response.status);
  }
  return response.blob();
}

/* ------------------------------------------------------- Lokale KI-Laufzeiten */

/**
 * Die bekannten lokalen Laufzeiten mit ihren Adressen und der Reihenfolge.
 *
 * Bewusst **ohne** Demo-Fallback, wie bei allen anderen Lerndaten: eine erfundene
 * Adresse im Formular waere eine Luege, die man erst beim Speichern bemerkt.
 * Der Aufrufer bekommt `null` und sagt das in der Oberflaeche.
 */
export async function getAiRuntime(): Promise<AiRuntimePayload | null> {
  return parseAiRuntime(await apiRequest("/v1/ai-runtime"));
}

/* ------------------------------------------------------------ Eigene Daten */

/**
 * Die Datenauskunft als ZIP. Nicht ueber `apiRequest`, weil die Antwort kein
 * JSON ist - der Fehlerfall dagegen schon, deshalb dieselbe Meldungslogik.
 */
export async function downloadAccountExport(): Promise<{ blob: Blob; filename: string }> {
  let response: Response;
  try {
    response = await fetch(endpoint("/v1/account/export"), { cache: "no-store", credentials: "include", headers: withLanguage(new Headers()) });
  } catch {
    throw new ApiError(t("Die Verbindung zum Lernraum konnte nicht hergestellt werden."), 0);
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => undefined);
    throw new ApiError(responseMessage(payload, t("Der Export ist fehlgeschlagen ({status}).", { status: response.status })), response.status);
  }
  const disposition = response.headers.get("content-disposition") ?? "";
  const filename = /filename="([^"]+)"/.exec(disposition)?.[1] ?? "lernraum-daten.zip";
  return { blob: await response.blob(), filename };
}

export async function deleteAccount(confirm: string, password?: string): Promise<void> {
  await apiRequest("/v1/account/delete", {
    method: "POST",
    body: JSON.stringify(password ? { confirm, password } : { confirm }),
  });
}
