"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, getScheduleRange } from "./api";
import type { DashboardLesson } from "./types";

type ScheduleStatus = "loading" | "ready" | "error";

interface ScheduleState {
  lessons: DashboardLesson[] | null;
  error: string | null;
  status: ScheduleStatus;
}

const initialState: ScheduleState = { lessons: null, error: null, status: "loading" };

const CHOICE_KEY = "lernraum-stundenplan-auswahl";

/** Parallel liegende Faecher, je Slot eine eigene Auswahl. */

export function useParallelChoice() {
  const [choices, setChoices] = useState<Record<string, string>>({});

  useEffect(() => {
    // Ueber einen Timeout statt direkt, sonst feuert der Effekt zwei Render
    // nacheinander ab. Das Muster ist dasselbe wie in lib/use-now.ts.
    const timer = window.setTimeout(() => {
      try {
        const raw = window.localStorage.getItem(CHOICE_KEY);
        if (raw) {
          const parsed: unknown = JSON.parse(raw);
          if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
            const eintraege: Record<string, string> = {};
            for (const [slot, id] of Object.entries(parsed as Record<string, unknown>)) {
              if (typeof id === "string") {
                eintraege[slot] = id;
              }
            }
            setChoices(eintraege);
          }
        }
      } catch {
        // Kaputter oder gesperrter Storage darf die Auswahl nicht blockieren.
      }
    }, 0);
    return () => window.clearTimeout(timer);
  }, []);

  const choose = useCallback((slotKey: string, lessonId: string) => {
    setChoices((aktuell) => {
      const naechste = { ...aktuell, [slotKey]: lessonId };
      try {
        window.localStorage.setItem(CHOICE_KEY, JSON.stringify(naechste));
      } catch {
        // Ohne Persistenz gilt die Auswahl fuer diese Sitzung.
      }
      return naechste;
    });
  }, []);

  return { choices, choose };
}

function errorMessage(error: unknown) {
  if (error instanceof ApiError) {
    return error.message;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return "Der Stundenplan konnte nicht geladen werden.";
}

/**
 * Laedt den Stundenplan fuer einen beliebigen Zeitraum. Leere Grenzen (ISO-Datum)
 * laden nichts, das deckt den ersten Render ab, bevor "heute" feststeht.
 */
export function useScheduleRange(startIso: string, endIso: string) {
  const requestId = useRef(0);
  const [state, setState] = useState<ScheduleState>(initialState);

  const load = useCallback(async () => {
    if (!startIso || !endIso) {
      return;
    }
    const currentRequest = ++requestId.current;
    setState((current) => ({ ...current, lessons: null, error: null, status: "loading" }));

    try {
      const result = await getScheduleRange(startIso, endIso);
      if (currentRequest !== requestId.current) {
        return;
      }
      setState({ lessons: result.lessons, error: null, status: "ready" });
    } catch (error) {
      if (currentRequest !== requestId.current) {
        return;
      }
      setState((current) => ({ ...current, lessons: [], error: errorMessage(error), status: "error" }));
    }
  }, [startIso, endIso]);

  useEffect(() => {
    void load();
    return () => {
      requestId.current += 1;
    };
  }, [load]);

  const retry = useCallback(() => {
    void load();
  }, [load]);

  return { ...state, reload: retry };
}
