"use client";

import { z } from "zod";
import { normalizeSource, type AnswerSource } from "./ai-runtime";
import { apiRequest } from "./api";

/*
 * Der Lern-Assistent (Startseite `/`): Chat, Vorschlaege und Gedaechtnis.
 *
 * Eigene Datei statt `api.ts`/`schemas.ts`: der Bereich ist abgeschlossen, und
 * zwei Wellen, die gleichzeitig an `api.ts` anbauen, kommen sich dort am
 * ehesten in die Quere.
 */

export type ChatRole = "user" | "assistant";
export type ActionKind = "exam" | "homework" | "event";

const sourceSchema = z.object({
  document_id: z.number().int(),
  filename: z.string(),
  chunk_index: z.number().int(),
  content: z.string(),
});

const proposalSchema = z.object({
  kind: z.enum(["exam", "homework", "event"]),
  title: z.string(),
  starts_at: z.string(),
  ends_at: z.string().nullable().optional().default(null),
  all_day: z.boolean().default(false),
  subject_id: z.number().int().nullable().optional().default(null),
  subject_name: z.string().nullable().optional().default(null),
  description: z.string().nullable().optional().default(null),
});

const memorySchema = z.object({
  id: z.number().int(),
  content: z.string(),
  source: z.string(),
  created_at: z.string(),
});

/** Was der Assistent selbst erledigt hat (``auto_apply``), mit ``undoable`` fuer „Rückgängig“. */
const doneSchema = z.object({
  kind: z.enum(["exam", "homework", "event", "homework_done", "week_plan"]),
  id: z.number().int(),
  title: z.string(),
  message: z.string(),
  undoable: z.boolean(),
});

const chatResponseSchema = z.object({
  reply: z.string(),
  source: z.string().transform((value) => normalizeSource(value)),
  model: z.string().nullable().optional().default(null),
  sources: z.array(sourceSchema),
  proposals: z.array(proposalSchema),
  memories_added: z.array(memorySchema),
  grades_shared: z.boolean().default(false),
  training_example_id: z.number().int().nullable().optional().default(null),
  done: z.array(doneSchema).optional().default([]),
});

const routeSchema = z.object({
  integration_id: z.number().int().nullable().optional().default(null),
  local_model_id: z.string().nullable().optional().default(null),
  name: z.string(),
  model: z.string(),
  source: z.string(),
  selected: z.boolean().default(false),
});

const appliedSchema = z.object({
  kind: z.enum(["exam", "homework", "event"]),
  id: z.number().int(),
  title: z.string(),
  message: z.string(),
});

export type AssistantProposal = z.infer<typeof proposalSchema>;
export type AssistantMemory = z.infer<typeof memorySchema>;
export type AssistantRoute = z.infer<typeof routeSchema>;
export type AssistantSource = z.infer<typeof sourceSchema>;
export type AppliedAction = z.infer<typeof appliedSchema>;
export type AgentStep = z.infer<typeof doneSchema>;
export interface AssistantReply extends Omit<z.infer<typeof chatResponseSchema>, "source"> {
  source: AnswerSource;
}

export interface ChatMessage {
  role: ChatRole;
  content: string;
}

/** Welches Modell antwortet: automatisch, ein geladenes (eingebautes) oder eine eingetragene Verbindung. */
export type RouteChoice = { kind: "auto" } | { kind: "local"; modelId: string } | { kind: "integration"; id: number };

export function routeKey(route: AssistantRoute): string {
  return route.local_model_id ? `local:${route.local_model_id}` : `int:${route.integration_id}`;
}

export function parseRouteKey(key: string): RouteChoice {
  if (key.startsWith("local:") && key.length > 6) {
    return { kind: "local", modelId: key.slice(6) };
  }
  const id = Number(key.slice(4));
  if (key.startsWith("int:") && Number.isInteger(id)) {
    return { kind: "integration", id };
  }
  return { kind: "auto" };
}

/**
 * `autoApply`: der Assistent handelt selbst - Vorschlaege werden gleich
 * eingetragen, "Plan meine Woche" und "Mathe erledigt" sofort ausgefuehrt.
 */
export async function sendAssistantMessage(messages: ChatMessage[], choice: RouteChoice, voice = false, autoApply = false): Promise<AssistantReply> {
  const payload = await apiRequest("/v1/assistant/chat", {
    method: "POST",
    body: JSON.stringify({
      messages,
      integration_id: choice.kind === "integration" ? choice.id : null,
      local_model_id: choice.kind === "local" ? choice.modelId : null,
      voice,
      auto_apply: autoApply,
    }),
  });
  return chatResponseSchema.parse(payload);
}

/** Nimmt einen selbst erledigten Schritt zurueck (Eintrag loeschen, Hausaufgabe wieder offen). */
export async function undoAgentStep(step: AgentStep, trainingExampleId: number | null = null): Promise<void> {
  await apiRequest("/v1/assistant/actions/undo", {
    method: "POST",
    body: JSON.stringify({ kind: step.kind, id: step.id, training_example_id: trainingExampleId }),
  });
}

export async function getAssistantRoutes(): Promise<AssistantRoute[]> {
  return z.array(routeSchema).parse(await apiRequest("/v1/assistant/routes"));
}

/** `trainingExampleId` aus der Chat-Antwort: der Eintrag wird dort als Signal vermerkt (nur mit Trainingsfreigabe). */
export async function applyAssistantProposal(proposal: AssistantProposal, trainingExampleId: number | null = null): Promise<AppliedAction> {
  const payload = await apiRequest("/v1/assistant/actions", {
    method: "POST",
    body: JSON.stringify({ ...proposal, training_example_id: trainingExampleId }),
  });
  return appliedSchema.parse(payload);
}

export async function getAssistantMemories(): Promise<AssistantMemory[]> {
  return z.array(memorySchema).parse(await apiRequest("/v1/assistant/memories"));
}

export async function addAssistantMemory(content: string): Promise<AssistantMemory> {
  return memorySchema.parse(await apiRequest("/v1/assistant/memories", { method: "POST", body: JSON.stringify({ content }) }));
}

export async function deleteAssistantMemory(id: number): Promise<void> {
  await apiRequest(`/v1/assistant/memories/${encodeURIComponent(String(id))}`, { method: "DELETE" });
}

export const ACTION_LABELS: Record<ActionKind, string> = {
  exam: "Prüfung",
  homework: "Hausaufgabe",
  event: "Termin",
};

/** Wann der Vorschlag liegt, in deutscher Ortszeit - so, wie er eingetragen wird. */
export function describeProposalTime(proposal: Pick<AssistantProposal, "kind" | "starts_at" | "ends_at" | "all_day">, timeZone?: string): string {
  const start = new Date(proposal.starts_at);
  if (Number.isNaN(start.getTime())) {
    return "Datum unklar";
  }
  const tag = new Intl.DateTimeFormat("de-DE", { weekday: "short", day: "2-digit", month: "2-digit", year: "numeric", timeZone }).format(start);
  if (proposal.all_day) {
    return `${tag}, ganztägig`;
  }
  const uhr = (value: Date) => new Intl.DateTimeFormat("de-DE", { hour: "2-digit", minute: "2-digit", timeZone }).format(value);
  const prefix = proposal.kind === "homework" ? "fällig " : "";
  if (proposal.kind === "event" && proposal.ends_at) {
    const ende = new Date(proposal.ends_at);
    if (!Number.isNaN(ende.getTime())) {
      return `${tag}, ${uhr(start)}–${uhr(ende)}`;
    }
  }
  return `${prefix}${tag}, ${uhr(start)}`;
}

/**
 * Ob eine kurze gesprochene Antwort einen offenen Vorschlag bestaetigt oder ablehnt.
 *
 * Nur kurze Saetze zaehlen: "Ja, und was ist mit Mathe?" ist eine neue Frage und
 * geht an das Modell, nicht an den Eintrag.
 */
export function bestaetigung(text: string): "ja" | "nein" | null {
  const satz = text.toLowerCase().replace(/[.,!?…]/g, " ").replace(/\s+/g, " ").trim();
  if (!satz || satz.split(" ").length > 6) {
    return null;
  }
  if (/^(nein|nee|ne|nö|lieber nicht|doch nicht|nicht eintragen|abbrechen|verwerfen|stopp)\b/.test(satz)) {
    return "nein";
  }
  if (/^(ja|jo|jap|jawohl|ok|okay|passt|genau|gerne|gern|klar|richtig|stimmt|mach das|mach|bitte|trag (es|sie|das|ihn|alles) ein|eintragen|ja bitte)\b/.test(satz)) {
    return "ja";
  }
  return null;
}

/** Der Verlauf, der an das Backend geht: nur Rollen und Text, die letzten Zuege. */
export function historyForRequest(messages: ChatMessage[], limit = 12): ChatMessage[] {
  return messages.filter((message) => message.content.trim()).slice(-limit);
}

const VERLAUF_KEY = "lernraum-assistent-verlauf";

/** Der Verlauf liegt nur in diesem Browser. Gespeichert wird er nicht auf dem Server. */
export function loadHistory(): ChatMessage[] {
  try {
    const raw = window.localStorage.getItem(VERLAUF_KEY);
    if (!raw) {
      return [];
    }
    const parsed = z.array(z.object({ role: z.enum(["user", "assistant"]), content: z.string() })).safeParse(JSON.parse(raw));
    return parsed.success ? parsed.data.slice(-40) : [];
  } catch {
    return [];
  }
}

export function saveHistory(messages: ChatMessage[]): void {
  try {
    window.localStorage.setItem(VERLAUF_KEY, JSON.stringify(messages.slice(-40)));
  } catch {
    // Privates Fenster oder voller Speicher: dann eben ohne Verlauf.
  }
}

export function clearHistory(): void {
  try {
    window.localStorage.removeItem(VERLAUF_KEY);
  } catch {
    // siehe saveHistory
  }
}

// --- Trainingsbeispiele (Vorstufe zum Feintuning je Nutzer) -----------------

const trainingHardwareSchema = z.object({
  eligible: z.boolean(),
  backend: z.enum(["mlx", "cuda"]).nullable().optional().default(null),
  reason: z.string(),
  system: z.string(),
  machine: z.string(),
  ram_gb: z.number().nullable().optional().default(null),
  gpu_name: z.string().nullable().optional().default(null),
  vram_gb: z.number().nullable().optional().default(null),
});

const trainingStatusSchema = z.object({
  enabled: z.boolean(),
  auto: z.boolean().optional().default(false),
  materials: z.boolean().optional().default(false),
  max_examples: z.number().int(),
  stats: z.object({
    total: z.number().int(),
    good: z.number().int(),
    bad: z.number().int(),
    corrected: z.number().int(),
    unlabeled: z.number().int(),
  }),
  hardware: trainingHardwareSchema,
});

const trainingLabelSchema = z.enum(["good", "bad", "corrected"]);

const trainingExampleSchema = z.object({
  id: z.number().int(),
  created_at: z.string(),
  question: z.string(),
  completion: z.string(),
  source: z.string(),
  model: z.string().nullable().optional().default(null),
  label: trainingLabelSchema.nullable(),
  user_rating: z.enum(["good", "bad"]).nullable(),
  applied_actions: z.number().int(),
  rejected_actions: z.number().int(),
  memory_deleted: z.boolean(),
  server_adjusted: z.boolean(),
});

export type TrainingStatus = z.infer<typeof trainingStatusSchema>;
export type TrainingExample = z.infer<typeof trainingExampleSchema>;
export type TrainingLabel = z.infer<typeof trainingLabelSchema>;
export type TrainingFeedbackEvent = "rejected" | "good" | "bad" | "cleared";

export const TRAINING_LABELS: Record<TrainingLabel, string> = {
  good: "Gut",
  bad: "Daneben",
  corrected: "Korrigiert",
};

export async function getTrainingStatus(): Promise<TrainingStatus> {
  return trainingStatusSchema.parse(await apiRequest("/v1/assistant/training"));
}

export async function setTrainingConsent(enabled: boolean): Promise<TrainingStatus> {
  const payload = await apiRequest("/v1/assistant/training/consent", { method: "PUT", body: JSON.stringify({ enabled }) });
  return trainingStatusSchema.parse(payload);
}

export async function getTrainingExamples(limit = 20): Promise<TrainingExample[]> {
  return z.array(trainingExampleSchema).parse(await apiRequest(`/v1/assistant/training/examples?limit=${limit}`));
}

export async function sendTrainingFeedback(exampleId: number, event: TrainingFeedbackEvent): Promise<void> {
  await apiRequest(`/v1/assistant/training/examples/${encodeURIComponent(String(exampleId))}/feedback`, {
    method: "POST",
    body: JSON.stringify({ event }),
  });
}

export async function deleteTrainingExample(exampleId: number): Promise<void> {
  await apiRequest(`/v1/assistant/training/examples/${encodeURIComponent(String(exampleId))}`, { method: "DELETE" });
}

export async function deleteAllTrainingExamples(): Promise<void> {
  await apiRequest("/v1/assistant/training/examples", { method: "DELETE" });
}

export async function setMaterialsTraining(enabled: boolean): Promise<TrainingStatus> {
  const payload = await apiRequest("/v1/assistant/training/materials", { method: "PUT", body: JSON.stringify({ enabled }) });
  return trainingStatusSchema.parse(payload);
}

export async function setAutoTraining(enabled: boolean): Promise<TrainingStatus> {
  const payload = await apiRequest("/v1/assistant/training/auto", { method: "PUT", body: JSON.stringify({ enabled }) });
  return trainingStatusSchema.parse(payload);
}

// --- Trainingsrunden und Adapter --------------------------------------------

const trainingRunSchema = z.object({
  run_id: z.string(),
  base_model_id: z.string(),
  status: z.enum(["pending", "loading", "generating", "running", "paused", "converting", "done", "failed"]),
  active: z.boolean(),
  step: z.number().int(),
  total_steps: z.number().int(),
  example_count: z.number().int(),
  loss: z.number().nullable().optional().default(null),
  eval_loss_base: z.number().nullable().optional().default(null),
  eval_loss: z.number().nullable().optional().default(null),
  eval_score_base: z.number().nullable().optional().default(null),
  eval_score: z.number().nullable().optional().default(null),
  qa_done: z.number().int().optional().default(0),
  qa_total: z.number().int().optional().default(0),
  qa_kept: z.number().int().optional().default(0),
  seconds_per_step: z.number().nullable().optional().default(null),
  skipped_too_long: z.number().int().optional().default(0),
  error: z.string().nullable().optional().default(null),
  adapter_version: z.number().int().nullable().optional().default(null),
  activated: z.boolean().optional().default(false),
  created_at: z.string().nullable().optional().default(null),
  updated_at: z.string().nullable().optional().default(null),
});

const adaptersSchema = z.object({
  model_id: z.string(),
  active_version: z.number().int().nullable(),
  versions: z.array(
    z.object({
      version: z.number().int(),
      example_count: z.number().int(),
      eval_score: z.number().nullable().optional().default(null),
      eval_loss: z.number().nullable().optional().default(null),
      eval_loss_base: z.number().nullable().optional().default(null),
      created_at: z.string(),
      active: z.boolean(),
    }),
  ),
});

export type TrainingRun = z.infer<typeof trainingRunSchema>;
export type TrainingAdapters = z.infer<typeof adaptersSchema>;

export const TRAINING_RUN_STATUS: Record<TrainingRun["status"], string> = {
  pending: "Wartet auf den Start",
  loading: "Lädt das Modell",
  generating: "Übt mit deinem Material",
  running: "Trainiert",
  paused: "Pausiert",
  converting: "Prüft und speichert",
  done: "Fertig",
  failed: "Abgebrochen",
};

export async function getTrainingRun(): Promise<TrainingRun | null> {
  return trainingRunSchema.nullable().parse(await apiRequest("/v1/assistant/training/run"));
}

export async function startTrainingRun(): Promise<TrainingRun> {
  return trainingRunSchema.parse(await apiRequest("/v1/assistant/training/run", { method: "POST", body: "{}" }));
}

export async function pauseTrainingRun(): Promise<TrainingRun | null> {
  return trainingRunSchema.nullable().parse(await apiRequest("/v1/assistant/training/run/pause", { method: "POST" }));
}

export async function discardTrainingRun(): Promise<void> {
  await apiRequest("/v1/assistant/training/run", { method: "DELETE" });
}

export async function getTrainingAdapters(): Promise<TrainingAdapters> {
  return adaptersSchema.parse(await apiRequest("/v1/assistant/training/adapters"));
}

export async function rollbackTrainingAdapter(): Promise<TrainingAdapters> {
  return adaptersSchema.parse(await apiRequest("/v1/assistant/training/adapters/rollback", { method: "POST" }));
}

// --- Trainer-Umgebung (einmal einrichten, dann trainieren) ------------------

const trainerSetupSchema = z.object({
  state: z.enum(["unsupported", "missing", "installing", "ready", "outdated", "error"]),
  phase: z.string().nullable().optional().default(null),
  phase_label: z.string().nullable().optional().default(null),
  supported: z.boolean(),
  reason: z.string().nullable().optional().default(null),
  error: z.string().nullable().optional().default(null),
  log_tail: z.array(z.string()).default([]),
  approx_bytes: z.number().int(),
  location: z.string(),
  torch_version: z.string().nullable().optional().default(null),
  installed_at: z.string().nullable().optional().default(null),
  manual_python: z.boolean().default(false),
});

export type TrainerSetup = z.infer<typeof trainerSetupSchema>;

export async function getTrainerSetup(): Promise<TrainerSetup> {
  return trainerSetupSchema.parse(await apiRequest("/v1/assistant/training/setup"));
}

export async function startTrainerSetup(): Promise<TrainerSetup> {
  return trainerSetupSchema.parse(await apiRequest("/v1/assistant/training/setup", { method: "POST" }));
}

export async function cancelTrainerSetup(): Promise<TrainerSetup> {
  return trainerSetupSchema.parse(await apiRequest("/v1/assistant/training/setup/cancel", { method: "POST" }));
}

export async function removeTrainerSetup(): Promise<TrainerSetup> {
  return trainerSetupSchema.parse(await apiRequest("/v1/assistant/training/setup", { method: "DELETE" }));
}

/** Trainieren geht mit fertiger Umgebung - auch mit veralteter, bis jemand aktualisiert. */
export function trainerBereit(setup: TrainerSetup | null): boolean {
  return setup !== null && (setup.state === "ready" || setup.state === "outdated");
}

/** Gigabyte mit einer Nachkommastelle, deutsch. */
export function gigabyte(bytes: number): string {
  return `${(bytes / 1024 ** 3).toLocaleString("de-DE", { maximumFractionDigits: 1 })} GB`;
}

/** Anteil als ganze Prozent, `null` als Gedankenstrich. */
export function prozent(wert: number | null): string {
  return wert === null ? "–" : `${Math.round(wert * 100)} %`;
}

/**
 * Ein Signal, das nur eine Zugabe ist: scheitert es, merkt die Person nichts
 * davon - der Eintrag oder das Verwerfen selbst ist ja passiert.
 */
export function feedbackNebenbei(exampleId: number | null | undefined, event: TrainingFeedbackEvent): void {
  if (exampleId == null) {
    return;
  }
  void sendTrainingFeedback(exampleId, event).catch(() => undefined);
}

// --- Lernprofil und Abfrage --------------------------------------------------

const learnerProfileSchema = z.object({ lines: z.array(z.string()) });

const quizQuestionSchema = z.object({
  question_id: z.number().int(),
  question: z.string(),
  document_id: z.number().int(),
  filename: z.string(),
  source: z.string(),
});

const quizResultSchema = z.object({
  verdict: z.enum(["richtig", "teilweise", "falsch"]),
  explanation: z.string(),
  reference: z.string(),
  excerpt: z.string(),
  filename: z.string(),
  source: z.string(),
  topic: z.string().nullable().optional().default(null),
  confidence_label: z.string().nullable().optional().default(null),
});

export type QuizQuestion = z.infer<typeof quizQuestionSchema>;
export type QuizResult = z.infer<typeof quizResultSchema>;

/** Genau die Zeilen, die bei jeder Frage als „Lernprofil“ im Prompt stehen. */
export async function getLearnerProfile(): Promise<string[]> {
  return learnerProfileSchema.parse(await apiRequest("/v1/assistant/profile")).lines;
}

export async function startQuiz(subjectId: number | null): Promise<QuizQuestion> {
  const payload = await apiRequest("/v1/assistant/quiz", { method: "POST", body: JSON.stringify({ subject_id: subjectId }) });
  return quizQuestionSchema.parse(payload);
}

export async function answerQuiz(questionId: number, answer: string): Promise<QuizResult> {
  const payload = await apiRequest(`/v1/assistant/quiz/questions/${encodeURIComponent(String(questionId))}/answer`, {
    method: "POST",
    body: JSON.stringify({ answer }),
  });
  return quizResultSchema.parse(payload);
}
