"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  authorizeIntegration as authorizeIntegrationRequest,
  createCalendarFeed,
  createIntegration,
  deleteIntegration,
  getAiRuntime,
  getCalendarFeed,
  getCalendarWriting,
  getIntegrationCatalog,
  getIntegrations,
  getSession,
  setCalendarWriting,
  syncIntegration,
  testIntegration,
  updateIntegration,
  type IntegrationChanges,
  type IntegrationWrite,
} from "./api";
import type { AiRuntimePayload } from "./ai-runtime";
import type {
  CalendarFeed,
  CalendarWriting,
  IntegrationCatalog,
  IntegrationRead,
  IntegrationSyncResult,
  IntegrationTestResult,
  UserResponse,
} from "./types";
import { t } from "./i18n";

type SettingsStatus = "loading" | "ready" | "error";

interface SettingsState {
  user: UserResponse | null;
  integrations: IntegrationRead[];
  catalog: IntegrationCatalog[];
  /**
   * Die bekannten lokalen KI-Laufzeiten, oder `null`.
   *
   * Eigener Fehlerpfad: faellt diese Abfrage aus, sind die Vorschlaege im
   * Formular weg - die restlichen Einstellungen stehen aber. Deshalb kein
   * `Promise.all`, das den ganzen Bildschirm mitreisst.
   */
  runtime: AiRuntimePayload | null;
  /** Die Zustimmung zum Schreiben in verbundene Kalender. */
  writing: CalendarWriting | null;
  /** Die abonnierbare ICS-Adresse. */
  feed: CalendarFeed | null;
  error: string | null;
  status: SettingsStatus;
}

const initialState: SettingsState = {
  user: null,
  integrations: [],
  catalog: [],
  runtime: null,
  writing: null,
  feed: null,
  error: null,
  status: "loading",
};


function errorMessage(error: unknown) {
  if (error instanceof ApiError) {
    return error.message;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return t("Die Aktion ist fehlgeschlagen. Bitte versuche es erneut.");
}

function sortIntegrations(integrations: IntegrationRead[]) {
  return [...integrations].sort((a, b) => a.kind.localeCompare(b.kind) || a.name.localeCompare(b.name, "de") || a.id - b.id);
}

export function useIntegrations() {
  const requestId = useRef(0);
  const [state, setState] = useState<SettingsState>(initialState);
  const [pendingAction, setPendingAction] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<number, IntegrationTestResult>>({});
  const [syncResults, setSyncResults] = useState<Record<number, IntegrationSyncResult>>({});

  const load = useCallback(async () => {
    const currentRequest = ++requestId.current;
    setState((current) => ({ ...current, error: null, status: "loading" }));

    try {
      const user = await getSession();
      if (currentRequest !== requestId.current) {
        return;
      }
      if (!user) {
        setState((current) => ({ ...current, status: "error", error: t("Es ist kein Benutzerkonto vorhanden.") }));
        return;
      }

      const [integrations, catalog, writing, feed] = await Promise.all([
        getIntegrations(),
        getIntegrationCatalog(),
        getCalendarWriting(),
        getCalendarFeed(),
      ]);
      if (currentRequest !== requestId.current) {
        return;
      }
      /*
        Die Laufzeiten kommen **nach** dem Rest und mit eigenem Fehlerpfad: sie
        sind ein Vorschlag im Formular, kein Zustand, ohne den die Einstellungen
        unbrauchbar waeren. Versagt der Abruf, bleibt `runtime` `null` und die
        Karte sagt das - statt den ganzen Bildschirm zu leeren.
      */
      const runtime = await getAiRuntime().catch(() => null);
      if (currentRequest !== requestId.current) {
        return;
      }
      setState({ user, integrations, catalog, runtime, writing, feed, error: null, status: "ready" });
    } catch (error) {
      if (currentRequest !== requestId.current) {
        return;
      }
      if (error instanceof ApiError && error.status === 401) {
        setState((current) => ({ ...current, status: "error", error: t("Die Anmeldung ist deaktiviert, aber der Server verlangt sie.") }));
        return;
      }
      setState((current) => ({ ...current, error: errorMessage(error), status: "error" }));
    }
  }, []);

  useEffect(() => {
    void load();
    return () => {
      requestId.current += 1;
    };
  }, [load]);

  const retry = useCallback(() => {
    setActionError(null);
    void load();
  }, [load]);

  const refreshIntegrations = useCallback(async () => {
    try {
      const integrations = await getIntegrations();
      setState((current) => ({ ...current, integrations }));
    } catch (error) {
      setActionError(errorMessage(error));
    }
  }, []);

  const runAction = useCallback(
    async <T,>(key: string, action: () => Promise<T>, onSuccess: (result: T) => void | Promise<void>) => {
      setPendingAction(key);
      setActionError(null);
      try {
        const result = await action();
        await onSuccess(result);
        return true;
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) {
          setActionError(t("Die Anmeldung ist deaktiviert, aber der Server verlangt sie."));
        } else {
          setActionError(errorMessage(error));
        }
        return false;
      } finally {
        setPendingAction(null);
      }
    },
    [],
  );

  const addIntegration = useCallback(
    (input: IntegrationWrite) =>
      runAction(
        "create",
        () => createIntegration(input),
        (created) => {
          setState((current) => ({ ...current, integrations: sortIntegrations([...current.integrations, created]) }));
        },
      ),
    [runAction],
  );

  const changeIntegration = useCallback(
    (integrationId: number, changes: IntegrationChanges) =>
      runAction(
        `update:${integrationId}`,
        () => updateIntegration(integrationId, changes),
        (updated) => {
          setState((current) => ({
            ...current,
            integrations: sortIntegrations(current.integrations.map((item) => (item.id === updated.id ? updated : item))),
          }));
        },
      ),
    [runAction],
  );

  const checkIntegration = useCallback(
    (integrationId: number) =>
      runAction(
        `test:${integrationId}`,
        () => testIntegration(integrationId),
        async (result) => {
          setTestResults((current) => ({ ...current, [integrationId]: result }));
          await refreshIntegrations();
        },
      ),
    [refreshIntegrations, runAction],
  );

  const runSync = useCallback(
    (integrationId: number) =>
      runAction(
        `sync:${integrationId}`,
        () => syncIntegration(integrationId),
        async (result) => {
          setSyncResults((current) => ({ ...current, [integrationId]: result }));
          await refreshIntegrations();
        },
      ),
    [refreshIntegrations, runAction],
  );

  /**
   * Startet den Einwilligungslauf von Google und lässt den Browser dort hin.
   *
   * Der Server liefert nur die Adresse. Das Weiterleiten passiert hier von
   * Hand, weil die Oberfläche vorher noch die Freigabe zum Schreiben zeigen
   * muss - und weil `window.location` nur im Browser existiert. In einem
   * Server-Render gäbe es das nicht, deshalb die Prüfung.
   *
   * Der Rückweg von Google endet beim **Backend** (`/v1/integrations/{id}/oauth/callback`),
   * nicht hier: der Autorisierungscode soll nicht durch den Anwendungszustand.
   * Das Backend legt das Token ab und zeigt eine Seite mit einem Link zurück
   * auf `/settings` - deshalb ist nach dem Hinweg ein Neuladen nötig, damit die
   * Verbindung mit dem Token sichtbar wird.
   */
  const authorizeIntegration = useCallback(
    (integrationId: number) =>
      runAction(`authorize:${integrationId}`, () => authorizeIntegrationRequest(integrationId), async (result) => {
        if (typeof window !== "undefined" && result.authorization_url) {
          window.location.assign(result.authorization_url);
        }
      }),
    [runAction],
  );

  const removeIntegration = useCallback(
    (integrationId: number) =>
      runAction(
        `delete:${integrationId}`,
        () => deleteIntegration(integrationId),
        () => {
          setState((current) => ({
            ...current,
            integrations: current.integrations.filter((item) => item.id !== integrationId),
          }));
          setTestResults((current) => {
            const next = { ...current };
            delete next[integrationId];
            return next;
          });
          setSyncResults((current) => {
            const next = { ...current };
            delete next[integrationId];
            return next;
          });
        },
      ),
    [runAction],
  );

  /**
   * Die Zustimmung zum Schreiben setzen oder zurücknehmen.
   *
   * Die Antwort wird **nicht** neu geladen: sie ist genau die Anzeige, die jetzt
   * gebraucht wird, und ein weiterer Abruf würde nur die gerade geschriebene
   * Entscheidung wieder einholen.
   */
  const toggleWriting = useCallback(
    (enabled: boolean) =>
      runAction("writing", () => setCalendarWriting(enabled), (writing) => {
        setState((current) => ({ ...current, writing }));
      }),
    [runAction],
  );

  /** Einen neuen Abo-Schlüssel erzeugen. Der alte ist danach ungültig. */
  const createFeed = useCallback(
    () =>
      runAction("feed", () => createCalendarFeed(), (feed) => {
        setState((current) => ({ ...current, feed }));
      }),
    [runAction],
  );

  return {
    ...state,
    pendingAction,
    actionError,
    testResults,
    syncResults,
    retry,
    addIntegration,
    changeIntegration,
    checkIntegration,
    runSync,
    authorizeIntegration,
    removeIntegration,
    toggleWriting,
    createFeed,
  };
}
