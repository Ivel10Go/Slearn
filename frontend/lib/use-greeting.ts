"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getGreeting, refreshGreeting as refreshGreetingRequest } from "./api";
import type { Greeting } from "./types";
import { t } from "./i18n";

type GreetingStatus = "loading" | "ready" | "error";

interface GreetingState {
  greeting: Greeting | null;
  error: string | null;
  status: GreetingStatus;
}

const initialState: GreetingState = { greeting: null, error: null, status: "loading" };

/**
 * Laedt den Willkommenssatz des Tages.
 *
 * Das Backend haelt den Satz selbst fuer den Tag, deshalb reicht hier ein
 * einziger Abruf beim Laden der Seite. `refreshing` ist getrennt von `status`,
 * damit die Kachel waehrend des Neuformulierens ihren Text behalt und nicht
 * in einen Ladezustand zurueckspringt.
 */
export function useGreeting() {
  const requestId = useRef(0);
  const [state, setState] = useState<GreetingState>(initialState);
  const [refreshing, setRefreshing] = useState(false);

  useEffect(() => {
    const currentRequest = ++requestId.current;
    const timer = window.setTimeout(() => {
      void getGreeting()
        .then((greeting) => {
          if (currentRequest !== requestId.current) {
            return;
          }
          setState({ greeting, error: null, status: "ready" });
        })
        .catch((error: unknown) => {
          if (currentRequest !== requestId.current) {
            return;
          }
          setState({
            greeting: null,
            error: error instanceof Error ? error.message : t("Der Willkommenssatz konnte nicht geladen werden."),
            status: "error",
          });
        });
    }, 0);
    return () => {
      window.clearTimeout(timer);
      requestId.current += 1;
    };
  }, []);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const greeting = await refreshGreetingRequest();
      setState({ greeting, error: null, status: "ready" });
    } catch (error) {
      setState((current) => ({
        ...current,
        error: error instanceof Error ? error.message : t("Der Satz konnte nicht erneuert werden."),
      }));
    } finally {
      setRefreshing(false);
    }
  }, []);

  return { ...state, refresh, refreshing };
}
