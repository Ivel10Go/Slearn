"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, getDashboard, getSession } from "./api";
import type { DashboardData, UserResponse } from "./types";

type DashboardStatus = "loading" | "ready" | "error";

interface DashboardState {
  data: DashboardData | null;
  user: UserResponse | null;
  error: string | null;
  status: DashboardStatus;
}

function errorMessage(error: unknown) {
  if (error instanceof ApiError) {
    return error.message;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return "Die Daten konnten nicht geladen werden.";
}

export function useDashboard() {
  const requestId = useRef(0);
  const [state, setState] = useState<DashboardState>({
    data: null,
    user: null,
    error: null,
    status: "loading",
  });

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

      const data = await getDashboard();
      if (currentRequest !== requestId.current) {
        return;
      }
      setState({ data, user, error: null, status: "ready" });
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
    void load();
  }, [load]);

  return { ...state, retry };
}
