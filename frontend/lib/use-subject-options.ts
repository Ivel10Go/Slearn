"use client";

import { useCallback, useEffect, useState } from "react";
import { getSubjectOptions } from "./api";
import { errorMessage } from "./use-collection";
import type { SubjectOption } from "./types";
import { t } from "./i18n";

/**
 * Die Fachaushwaehler fuer Hausaufgaben, Pruefungen und Lernaufgaben.
 *
 * Jede Sektion laedt ihre eigene Liste. Das kostet drei kleine Requests, aber
 * es vermeidet den gemeinsamen Zustand ueber Modulgrenzen, der drei
 * Formulare unsichtbar koppeln wuerde: wer unter `/faecher` ein Fach anlegt,
 * soll das nicht erst beim naechsten Seitenaufbau sehen - und eine Liste, die
 * waehrend des Tippens neu geladen wird, wuerde die Auswahl zuruecksetzen.
 */
export function useSubjectOptions() {
  const [state, setState] = useState<{ options: SubjectOption[]; status: "loading" | "ready" | "error"; error: string | null }>({
    options: [],
    status: "loading",
    error: null,
  });

  const abrufen = useCallback(async () => {
    try {
      setState({ options: await getSubjectOptions(), status: "ready", error: null });
    } catch (loadError) {
      setState({ options: [], status: "error", error: errorMessage(loadError, t("Die Fächer konnten nicht geladen werden.")) });
    }
  }, []);

  useEffect(() => {
    // `setTimeout(…, 0)`, weil `react-hooks/set-state-in-effect` keinen
    // Zustandswechsel direkt im Effektkoerper erlaubt. So machen es
    // `use-greeting.ts` und `use-now.ts` ebenfalls.
    const timer = window.setTimeout(() => void abrufen(), 0);
    return () => window.clearTimeout(timer);
  }, [abrufen]);

  /** Nur aus Ereignishandlern aufrufen - hier darf der Zustand sofort wechseln. */
  const reload = useCallback(async () => {
    setState((current) => ({ ...current, status: "loading", error: null }));
    await abrufen();
  }, [abrufen]);

  return { ...state, reload };
}
