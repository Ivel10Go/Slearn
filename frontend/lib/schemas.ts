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

// Minimal passthrough schemas for other data structures
export const dashboardApiResponseSchema = z.object({}).passthrough();
export type DashboardApiResponse = z.infer<typeof dashboardApiResponseSchema>;
export function parseDashboardApiResponse(payload: unknown): any {
  return dashboardApiResponseSchema.parse(payload);
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