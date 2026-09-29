"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  createIntegration,
  deleteIntegration,
  getIntegrationCatalog,
  getIntegrations,
  getSession,
  syncIntegration,
  testIntegration,
  updateIntegration,
  type IntegrationChanges,
  type IntegrationWrite,
} from "./api";
import type {
  IntegrationCatalog,
  IntegrationRead,
  IntegrationSyncResult,
  IntegrationTestResult,
  UserResponse,
} from "./types";

type SettingsStatus = "loading" | "ready" | "error";

interface SettingsState {
  user: UserResponse | null;
  integrations: IntegrationRead[];
  catalog: IntegrationCatalog[];
  error: string | null;
  status: SettingsStatus;
}

const initialState: SettingsState = {
  user: null,
  integrations: [],
  catalog: [],
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
  return "Die Aktion ist fehlgeschlagen. Bitte versuche es erneut.";
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
        setState((current) => ({ ...current, status: "error", error: "Es ist kein Benutzerkonto vorhanden." }));
        return;
      }

      const [integrations, catalog] = await Promise.all([getIntegrations(), getIntegrationCatalog()]);
      if (currentRequest !== requestId.current) {
        return;
      }
      setState({ user, integrations, catalog, error: null, status: "ready" });
    } catch (error) {
      if (currentRequest !== requestId.current) {
        return;
      }
      if (error instanceof ApiError && error.status === 401) {
        setState((current) => ({ ...current, status: "error", error: "Die Anmeldung ist deaktiviert, aber der Server verlangt sie." }));
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
          setActionError("Die Anmeldung ist deaktiviert, aber der Server verlangt sie.");
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
    removeIntegration,
  };
}
