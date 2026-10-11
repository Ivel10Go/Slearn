"use client";

import { z } from "zod";
import { apiRequest } from "./api";
import { t } from "./i18n";

/*
 * Der Wochenplaner (`/api/v1/study/planner`): verteilt offene Aufgaben,
 * Hausaufgaben, Klausurthemen und faellige Wiederholungen auf die freie Zeit neben Unterricht und
 * Terminen.
 *
 * Eigene Datei statt `api.ts`/`schemas.ts`, wie beim Assistenten: der Bereich
 * ist abgeschlossen, und echte Schemas statt Stubs gehoeren nicht in die
 * Datei, deren `any`-Obergrenze die CI zaehlt.
 */

export type PlannerItemKind = "task" | "homework" | "exam_topic" | "exam_review" | "review";

export interface PlannerOptions {
  days: number;
  earliest: string;
  weekend_earliest: string;
  latest: string;
  include_weekends: boolean;
  max_minutes_per_day: number;
  break_minutes: number;
  after_school_minutes: number;
  homework_minutes: number;
  exam_topic_minutes: number;
  exam_lead_days: number;
  replan_scheduled: boolean;
  include_reviews: boolean;
}

/** Dieselben Werte wie `PlannerRequest` im Backend. */
export const DEFAULT_PLANNER_OPTIONS: PlannerOptions = {
  days: 7,
  earliest: "14:00",
  weekend_earliest: "10:00",
  latest: "19:30",
  include_weekends: true,
  max_minutes_per_day: 120,
  break_minutes: 10,
  after_school_minutes: 30,
  homework_minutes: 30,
  exam_topic_minutes: 45,
  exam_lead_days: 10,
  replan_scheduled: false,
  include_reviews: true,
};

const kindSchema = z.enum(["task", "homework", "exam_topic", "exam_review", "review"]);

const blockSchema = z.object({
  kind: kindSchema,
  title: z.string(),
  start: z.string(),
  end: z.string(),
  duration_minutes: z.number().int(),
  priority: z.number().int(),
  deadline: z.string().nullable().optional().default(null),
  late: z.boolean().default(false),
  task_id: z.number().int().nullable().optional().default(null),
  homework_id: z.number().int().nullable().optional().default(null),
  exam_id: z.number().int().nullable().optional().default(null),
  topic_id: z.number().int().nullable().optional().default(null),
  subject_name: z.string().nullable().optional().default(null),
});

const busySchema = z.object({
  kind: z.enum(["lesson", "event", "task"]),
  title: z.string(),
  start: z.string(),
  end: z.string(),
});

const daySchema = z.object({
  date: z.string(),
  budget_minutes: z.number().int(),
  planned_minutes: z.number().int(),
  blocks: z.array(blockSchema),
  busy: z.array(busySchema),
});

const unplacedSchema = z.object({
  kind: kindSchema,
  title: z.string(),
  duration_minutes: z.number().int(),
  deadline: z.string().nullable().optional().default(null),
  reason: z.string(),
});

export const plannerResultSchema = z.object({
  applied: z.boolean(),
  study_plan_id: z.number().int().nullable().optional().default(null),
  window_start: z.string(),
  window_end: z.string(),
  days: z.array(daySchema),
  unplaced: z.array(unplacedSchema),
  planned_count: z.number().int(),
  created_count: z.number().int().default(0),
});

export type PlannerBlock = z.infer<typeof blockSchema>;
export type PlannerBusy = z.infer<typeof busySchema>;
export type PlannerDay = z.infer<typeof daySchema>;
export type PlannerResult = z.infer<typeof plannerResultSchema>;

export const KIND_LABEL: Record<PlannerItemKind, string> = {
  task: t("Aufgabe"),
  homework: t("Hausaufgabe"),
  exam_topic: t("Klausur"),
  exam_review: t("Klausur"),
  review: t("Wiederholung"),
};

async function post(path: "preview" | "apply", options: PlannerOptions): Promise<PlannerResult> {
  const payload = await apiRequest(`/v1/study/planner/${path}`, {
    method: "POST",
    body: JSON.stringify(options),
  });
  return plannerResultSchema.parse(payload);
}

export function previewWeekPlan(options: PlannerOptions) {
  return post("preview", options);
}

export function applyWeekPlan(options: PlannerOptions) {
  return post("apply", options);
}

/** Ein Block, den es als Aufgabe noch nicht gibt - er entsteht erst beim Uebernehmen. */
export function isNewBlock(block: PlannerBlock) {
  return block.kind !== "task" && block.task_id === null;
}

export interface PlanSummary {
  blocks: number;
  minutes: number;
  newBlocks: number;
  late: number;
  unplaced: number;
}

export function summarizePlan(result: PlannerResult): PlanSummary {
  const blocks = result.days.flatMap((day) => day.blocks);
  return {
    blocks: blocks.length,
    minutes: blocks.reduce((sum, block) => sum + block.duration_minutes, 0),
    newBlocks: blocks.filter(isNewBlock).length,
    late: blocks.filter((block) => block.late).length,
    unplaced: result.unplaced.length,
  };
}

const CLOCK = /^([01]\d|2[0-3]):[0-5]\d$/;

/**
 * Prueft die Eingaben, bevor sie zum Server gehen, und nennt den Fehler auf
 * Deutsch. Der Server prueft dasselbe noch einmal; hier geht es nur darum,
 * dass niemand eine 422 ohne Erklaerung sieht.
 */
export function validateOptions(options: PlannerOptions): string | null {
  for (const value of [options.earliest, options.weekend_earliest, options.latest]) {
    if (!CLOCK.test(value)) {
      return t("Bitte Uhrzeiten als HH:MM angeben.");
    }
  }
  if (options.latest <= options.earliest || options.latest <= options.weekend_earliest) {
    return t("Das Ende muss nach dem frühesten Beginn liegen.");
  }
  if (!Number.isFinite(options.max_minutes_per_day) || options.max_minutes_per_day < 15 || options.max_minutes_per_day > 600) {
    return t("Pro Tag sind 15 bis 600 Minuten möglich.");
  }
  return null;
}

const STORAGE_KEY = "lernraum.wochenplan.optionen";

/**
 * Die zuletzt benutzten Einstellungen. Nur eine Bequemlichkeit: fehlt der
 * Speicher oder ist der Inhalt kaputt, gelten die Voreinstellungen.
 */
export function loadOptions(): PlannerOptions {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return raw ? mergeOptions(JSON.parse(raw)) : DEFAULT_PLANNER_OPTIONS;
  } catch {
    return DEFAULT_PLANNER_OPTIONS;
  }
}

export function saveOptions(options: PlannerOptions) {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(options));
  } catch {
    // Privates Fenster oder voller Speicher - dann gilt beim naechsten Mal die Voreinstellung.
  }
}

/** Nur bekannte Schluessel mit passendem Typ uebernehmen; alles andere bleibt Voreinstellung. */
export function mergeOptions(stored: unknown): PlannerOptions {
  const merged: Record<string, unknown> = { ...DEFAULT_PLANNER_OPTIONS };
  if (typeof stored === "object" && stored !== null) {
    const record = stored as Record<string, unknown>;
    for (const [key, fallback] of Object.entries(DEFAULT_PLANNER_OPTIONS)) {
      if (typeof record[key] === typeof fallback) {
        merged[key] = record[key];
      }
    }
  }
  return merged as unknown as PlannerOptions;
}
