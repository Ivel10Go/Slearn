"use client";

import { getDemoDashboard, getDemoScheduleRange, isDemoFallbackEnabled } from "./demo";
import { isDemoTaskId, normalizeDashboard, normalizeGreeting, normalizeScheduleRange, normalizeStudyAction, type ApiIdentifier, type DashboardData, type Greeting, type ScheduleRange, type UserResponse } from "./types";
import { parseLoginResponse, parseUserResponse, parseUserSession, parseIntegrationArray, parseIntegrationCatalogArray, parseIntegrationRead, parseIntegrationSyncResult, parseIntegrationTestResult } from "./schemas";
import type { IntegrationCatalog, IntegrationRead, IntegrationSyncResult, IntegrationTestResult } from "./types";

const API_PREFIX = "/api/backend";

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
  redirectOnUnauthorized?: boolean;
}

function endpoint(path: string) {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  return `${API_PREFIX}${normalizedPath}`;
}

function responseMessage(payload: unknown, fallback: string) {
  if (typeof payload === "object" && payload !== null && "detail" in payload && typeof payload.detail === "string") {
    return payload.detail;
  }
  if (typeof payload === "object" && payload !== null && "message" in payload && typeof payload.message === "string") {
    return payload.message;
  }
  return fallback;
}

function redirectToLogin() {
  if (typeof window === "undefined" || window.location.pathname === "/login") {
    return;
  }
  const next = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  window.location.replace(`/login?next=${encodeURIComponent(next)}`);
}

export function canUseDemoFallback(error: unknown) {
  return isDemoFallbackEnabled() && error instanceof ApiError && (error.status === 0 || error.status === 503);
}

export async function apiRequest(path: string, options: ApiRequestOptions = {}): Promise<unknown> {
  if (typeof window === "undefined") {
    throw new ApiError("API-Anfragen sind nur im Browser verfügbar.", 0);
  }

  const { redirectOnUnauthorized = true, body, headers: suppliedHeaders, ...fetchOptions } = options;
  const headers = new Headers(suppliedHeaders);
  if (!headers.has("Accept")) {
    headers.set("Accept", "application/json");
  }
  if (body && !headers.has("Content-Type")) {
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
    throw new ApiError("Die Verbindung zum Lernraum konnte nicht hergestellt werden.", 0);
  }

  const payload = response.status === 204 ? undefined : await response.json().catch(() => undefined);

  if (!response.ok) {
    if (response.status === 401 && redirectOnUnauthorized) {
      redirectToLogin();
    }
    throw new ApiError(responseMessage(payload, `Die Anfrage ist fehlgeschlagen (${response.status}).`), response.status);
  }

  return payload;
}

export interface LoginCredentials {
  username: string;
  password: string;
}

export interface LoginResponse {
  user: UserResponse;
  access_token?: string;
  token_type?: string;
}

export async function login(credentials: LoginCredentials) {
  const payload = parseLoginResponse(await apiRequest("/v1/auth/login", {
    method: "POST",
    body: JSON.stringify(credentials),
    redirectOnUnauthorized: false,
  }));
  return "user" in payload ? payload : { user: payload };
}

export async function getSession() {
  try {
    const payload = parseUserSession(await apiRequest("/v1/auth/me", { redirectOnUnauthorized: false }));
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
      return { id: "demo-user", username: "Demo-Lernende", email: null };
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
    throw new ApiError("Die Lernaufgabe ist ungültig.", 422);
  }
  if (isDemoTaskId(taskId)) {
    throw new ApiError("Demo-Aufgaben können nicht als echte Lernsessions gestartet werden.", 422);
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

export async function updateIntegration(integrationId: number, changes: IntegrationChanges): Promise<IntegrationRead> {
  const payload = await apiRequest(`/v1/integrations/${integrationId}`, {
    method: "PATCH",
    body: JSON.stringify(changes),
  });
  return parseIntegrationRead(payload);
}

export async function deleteIntegration(integrationId: number): Promise<void> {
  await apiRequest(`/v1/integrations/${integrationId}`, { method: "DELETE" });
}

export async function testIntegration(integrationId: number): Promise<IntegrationTestResult> {
  const payload = await apiRequest(`/v1/integrations/${integrationId}/test`, {
    method: "POST",
    body: JSON.stringify({}),
  });
  return parseIntegrationTestResult(payload);
}

export async function syncIntegration(integrationId: number): Promise<IntegrationSyncResult> {
  const payload = await apiRequest(`/v1/integrations/${integrationId}/sync`, {
    method: "POST",
    body: JSON.stringify({}),
  });
  return parseIntegrationSyncResult(payload);
}

export async function logout(): Promise<void> {
  await apiRequest("/v1/auth/logout", {
    method: "POST",
    body: JSON.stringify({}),
    redirectOnUnauthorized: false,
  });
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
