"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getLearningInsights, getTopicProgress, saveTopicProgress, setWeeklyGoal } from "./api";
import type { LearningInsights, TopicProgress } from "./types";
import { errorMessage } from "./use-collection";

type ProgressStatus = "loading" | "ready" | "error";

export interface ProgressState {
  insights: LearningInsights | null;
  topics: TopicProgress[];
  status: ProgressStatus;
  /** Fehler des Ladevorgangs. Bleibt bestehen, auch wenn eine Aktion scheitert. */
  error: string | null;
  /** Topic-Id, die gerade gespeichert wird, oder `"goal"`. */
  pending: string | null;
  /** Meldung der letzten fehlgeschlagenen Aktion, getrennt vom Ladefehler. */
  actionError: string | null;
}

export interface ProgressApi {
  reload: () => Promise<void>;
  bewerten: (topicId: number, confidence: number) => Promise<TopicProgress | null>;
  wochenzielSetzen: (goalMinutes: number) => Promise<boolean>;
}

const ANFANG: ProgressState = { insights: null, topics: [], status: "loading", error: null, pending: null, actionError: null };

/**
 * Lernstand und Lernprotokoll in einem Hook.
 *
 * Zwei Getter parallel, weil die Karte beides zeigt und ein zweiter Ladevorgang
 * nur eine zweite Luecke in der Kachel waere.
 *
 * **Nach dem Speichern wird nicht neu geladen.** Eine Konfidenz aendert genau
 * eine Sache: `LearningProgress`. Die Lernzeit, die Serie und das Wochenziel
 * stehen in anderen Zeilen und koennen sich dadurch nicht verschieben. Das
 * Ziel zu aendern betrifft genau ein Feld in `insights.week` - auch das kommt
 * als Antwort zurueck und wird uebernommen. Damit bleibt die Karte stehen,
 * waehrend gespeichert wird, und die Reihenfolge der Themen springt nicht.
 *
 * `refreshKey` laesst die Kachel von aussen neu laden - etwa nachdem auf dem
 * Dashboard ein Fokusblock abgeschlossen wurde, denn dann haben sich Tageszeit
 * und Serie tatsaechlich geaendert.
 */
export function useProgress(refreshKey = 0): ProgressApi & ProgressState {
  const requestId = useRef(0);
  const [state, setState] = useState<ProgressState>(ANFANG);

  const abrufen = useCallback(async () => {
    const currentRequest = ++requestId.current;
    try {
      const [insights, topics] = await Promise.all([getLearningInsights(), getTopicProgress()]);
      if (currentRequest !== requestId.current) {
        return;
      }
      setState((current) => ({ ...current, insights, topics, error: null, status: "ready" }));
    } catch (loadError) {
      if (currentRequest !== requestId.current) {
        return;
      }
      setState((current) => ({ ...current, error: errorMessage(loadError, "Der Lernfortschritt konnte nicht geladen werden."), status: "error" }));
    }
  }, []);

  useEffect(() => {
    // Siehe `useCollection`: `setTimeout(…, 0)` umgeht
    // `react-hooks/set-state-in-effect`, das keinen synchronen Zustandswechsel
    // im Effektkoerper erlaubt.
    const timer = window.setTimeout(() => void abrufen(), 0);
    return () => {
      window.clearTimeout(timer);
      // Eine spaetere Antwort darf nach dem Unmount nichts mehr setzen.
      requestId.current += 1;
    };
  }, [abrufen, refreshKey]);

  const bewerten = useCallback(async (topicId: number, confidence: number) => {
    const schluessel = `topic:${topicId}`;
    setState((current) => ({ ...current, pending: schluessel, actionError: null }));
    try {
      // Kein `last_reviewed_at` im Body: das Backend traegt "jetzt" ein, weil
      // eine Einschaetzung ohne Datum keine Wiederholung ist.
      const gespeichert = await saveTopicProgress(topicId, { confidence });
      setState((current) => ({
        ...current,
        // Gezielt ersetzt, nicht neu geladen: das haelt die Sortierung des
        // Backends und damit die Anzeigereihenfolge der Themen bei.
        topics: current.topics.map((thema) => (thema.topic_id === topicId ? gespeichert : thema)),
      }));
      return gespeichert;
    } catch (actionError) {
      setState((current) => ({ ...current, actionError: errorMessage(actionError, "Der Lernstand konnte nicht gespeichert werden.") }));
      return null;
    } finally {
      setState((current) => (current.pending === schluessel ? { ...current, pending: null } : current));
    }
  }, []);

  const wochenzielSetzen = useCallback(async (goalMinutes: number) => {
    setState((current) => ({ ...current, pending: "goal", actionError: null }));
    try {
      const woche = await setWeeklyGoal(goalMinutes);
      setState((current) => ({ ...current, insights: current.insights ? { ...current.insights, week: woche } : current.insights }));
      return true;
    } catch (actionError) {
      setState((current) => ({ ...current, actionError: errorMessage(actionError, "Das Wochenziel konnte nicht gespeichert werden.") }));
      return false;
    } finally {
      setState((current) => (current.pending === "goal" ? { ...current, pending: null } : current));
    }
  }, []);

  const reload = useCallback(async () => {
    setState((current) => ({ ...current, status: "loading", error: null, actionError: null }));
    await abrufen();
  }, [abrufen]);

  return { ...state, reload, bewerten, wochenzielSetzen };
}
