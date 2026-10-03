"use client";

import { z } from "zod";
import { normalizeSource, type AnswerSource } from "./ai-runtime";
import { apiRequest } from "./api";

/*
 * Der Lern-Assistent (`/assistent`): Chat, Vorschlaege und Gedaechtnis.
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

const chatResponseSchema = z.object({
  reply: z.string(),
  source: z.string().transform((value) => normalizeSource(value)),
  model: z.string().nullable().optional().default(null),
  sources: z.array(sourceSchema),
  proposals: z.array(proposalSchema),
  memories_added: z.array(memorySchema),
  grades_shared: z.boolean().default(false),
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

export async function sendAssistantMessage(messages: ChatMessage[], choice: RouteChoice, voice = false): Promise<AssistantReply> {
  const payload = await apiRequest("/v1/assistant/chat", {
    method: "POST",
    body: JSON.stringify({
      messages,
      integration_id: choice.kind === "integration" ? choice.id : null,
      local_model_id: choice.kind === "local" ? choice.modelId : null,
      voice,
    }),
  });
  return chatResponseSchema.parse(payload);
}

export async function getAssistantRoutes(): Promise<AssistantRoute[]> {
  return z.array(routeSchema).parse(await apiRequest("/v1/assistant/routes"));
}

export async function applyAssistantProposal(proposal: AssistantProposal): Promise<AppliedAction> {
  const payload = await apiRequest("/v1/assistant/actions", { method: "POST", body: JSON.stringify(proposal) });
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
