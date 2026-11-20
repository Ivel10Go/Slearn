import { z } from "zod";
import { apiRequest } from "./api";

/*
 * Klassenarbeit als Arbeitsbereich (`/api/v1/exams/{id}/notebook`): Material,
 * Lernplan aus dem Material, Studio. Eigene Datei mit echten Schemas wie `planner.ts`.
 */

export const CREATION_KINDS = ["summary", "cheatsheet", "mindmap", "flashcards", "glossary", "practice_exam"] as const;
export type CreationKind = (typeof CREATION_KINDS)[number];

const documentSchema = z.object({
  id: z.number().int(),
  filename: z.string(),
  subject_id: z.number().int().nullable(),
  material_kind: z.string().nullable(),
  material_topic: z.string().nullable(),
  selected: z.boolean(),
  summary: z.boolean().default(false),
});

const creationSchema = z.object({
  id: z.number().int(),
  kind: z.string(),
  body: z.string(),
  items: z.array(z.record(z.string(), z.string())).nullable(),
  source: z.string(),
  created_at: z.string(),
});

const notebookSchema = z.object({
  exam_id: z.number().int(),
  documents: z.array(documentSchema),
  creations: z.array(creationSchema),
});

const planResultSchema = z.object({
  created_count: z.number().int(),
  skipped_count: z.number().int(),
  first_due_on: z.string().nullable(),
  last_due_on: z.string().nullable(),
});

export type NotebookDocument = z.infer<typeof documentSchema>;
export type Creation = z.infer<typeof creationSchema>;
export type Notebook = z.infer<typeof notebookSchema>;
export type NotebookPlanResult = z.infer<typeof planResultSchema>;

const base = (examId: number) => `/v1/exams/${examId}/notebook`;

export async function getNotebook(examId: number): Promise<Notebook> {
  return notebookSchema.parse(await apiRequest(base(examId)));
}

export async function setNotebookDocuments(examId: number, documentIds: number[], summaryIds: number[] = []): Promise<void> {
  await apiRequest(`${base(examId)}/documents`, {
    method: "PUT",
    body: JSON.stringify({ document_ids: documentIds, summary_ids: summaryIds }),
  });
}

export async function createCreation(examId: number, kind: CreationKind): Promise<Creation> {
  return creationSchema.parse(await apiRequest(`${base(examId)}/creations`, { method: "POST", body: JSON.stringify({ kind }) }));
}

export async function deleteCreation(examId: number, creationId: number): Promise<void> {
  await apiRequest(`${base(examId)}/creations/${creationId}`, { method: "DELETE" });
}

export async function createNotebookPlan(examId: number): Promise<NotebookPlanResult> {
  return planResultSchema.parse(await apiRequest(`${base(examId)}/plan`, { method: "POST" }));
}

/** Mindmap-Text -> Zeilen mit Tiefe. Zwei Leerzeichen (oder ein Tab) je Ebene; Aufzaehlungszeichen fallen weg. */
export function mindmapLines(body: string): { depth: number; text: string }[] {
  return body
    .split("\n")
    .filter((line) => line.trim())
    .map((line) => {
      const indent = line.replace(/\t/g, "  ").match(/^ */)?.[0].length ?? 0;
      const bullet = /^\s*[-*•]\s+/.test(line);
      const text = line.replace(/^\s*([-*•]|\d+[.)])\s+/, "").replace(/^#+\s*/, "").trim();
      return { depth: Math.floor(indent / 2) + (bullet ? 1 : 0), text };
    });
}
