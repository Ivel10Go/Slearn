"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  createGrade,
  deleteGrade,
  getGradeSharing,
  getGrades,
  getGradeSummary,
  importGrades,
  previewGradeImport,
  setGradeSharing,
  type GradeWrite,
} from "./api";
import type { Grade, GradeImportPreview, GradeImportResult, GradeKind, GradeSharing, GradeSummary } from "./types";
import { errorMessage } from "./use-collection";
import { t } from "./i18n";

type GradesStatus = "loading" | "ready" | "error";

export interface GradesState {
  summary: GradeSummary | null;
  /** Die letzten Noten, neueste zuerst - fuer die Liste in der Karte. */
  grades: Grade[];
  sharing: GradeSharing | null;
  status: GradesStatus;
  /** Fehler des Ladevorgangs. Bleibt bestehen, auch wenn eine Aktion scheitert. */
  error: string | null;
  /** Aktionsschluessel der laufenden Operation, z. B. ``create`` oder ``delete:7``. */
  pending: string | null;
  /** Meldung der letzten fehlgeschlagenen Aktion, getrennt vom Ladefehler. */
  actionError: string | null;
}

export interface ImportInput {
  documentId: number;
  subjectId?: number;
  defaultKind?: GradeKind;
  defaultWeight?: number;
  defaultDate?: string;
}

export interface GradesApi {
  reload: () => Promise<void>;
  /** Die Gewichtung, die noch aussteht. Sie gehoert in den Aufruf, nicht in die Datenbank. */
  projectedWeight: number;
  setProjectedWeight: (weight: number) => void;
  speichern: (input: GradeWrite) => Promise<Grade | null>;
  loeschen: (gradeId: number) => Promise<boolean>;
  freigabeSetzen: (enabled: boolean) => Promise<boolean>;
  vorschau: (input: ImportInput) => Promise<GradeImportPreview | null>;
  uebernehmen: (input: ImportInput & { keys: string[] }) => Promise<GradeImportResult | null>;
}

const ANFANG: GradesState = { summary: null, grades: [], sharing: null, status: "loading", error: null, pending: null, actionError: null };

/** Wie viele Noten die Karte in der Liste zeigt. */
const LISTEN_LIMIT = 8;

/**
 * Noten: Schnitt, Verlauf, Liste und Freigabe in einem Hook.
 *
 * Drei Getter parallel, weil die Karte alles drei fuer denselben Gegenstand
 * zeigt: eine Schnittzahl ohne die Liste, aus der sie stammt, waere eine
 * Behauptung, und die Freigabe gehoert an dieselbe Karte, weil sie die Frage
 * beantwortet, wohin diese Zahlen gehen duerfen.
 *
 * **Nach dem Speichern wird neu geladen, nicht gepatcht.** Anders als bei den
 * Hausaufgaben loescht sich hier beim Speichern eine ganze Frage: der Schnitt,
 * die Linie und die Hochrechnung aendern sich alle drei aus derselben
 * Bewegung. Ein lokal eingesetztes `average` waere eine Zahl, die es so nicht
 * gibt.
 *
 * `projectedWeight` gehoert in den **Aufruf** und nicht in die Datenbank: es
 * gibt in diesem Stack keine geplanten Noten, und eine gespeicherte
 * Hochrechnung waere eine Zusage, die niemand belegen kann. Die Aenderung
 * laedt die Antwort neu - wie bei `refreshKey` in `useProgress`.
 */
export function useGrades(): GradesApi & GradesState {
  const requestId = useRef(0);
  const [state, setState] = useState<GradesState>(ANFANG);
  const [projectedWeight, setGewicht] = useState(0);

  const abrufen = useCallback(async () => {
    const currentRequest = ++requestId.current;
    try {
      // Eine einzige Anfrage fuer alles Zeitabhaengige. Drei Abfragen wuerden
      // drei Moeglichkeiten schaffen, in denen die Karte einen halben Stand
      // zeigt - Schnitt aus der einen, Verlauf aus der anderen Anfrage.
      const filter = projectedWeight > 0 ? { projectedWeight } : {};
      const [summary, grades, sharing] = await Promise.all([
        getGradeSummary(filter),
        getGrades({ limit: LISTEN_LIMIT }),
        getGradeSharing(),
      ]);
      if (currentRequest !== requestId.current) {
        return;
      }
      setState((current) => ({ ...current, summary, grades, sharing, error: null, status: "ready" }));
    } catch (loadError) {
      if (currentRequest !== requestId.current) {
        return;
      }
      setState((current) => ({ ...current, error: errorMessage(loadError, t("Die Noten konnten nicht geladen werden.")), status: "error" }));
    }
  }, [projectedWeight]);

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
  }, [abrufen]);

  const run = useCallback(async <R,>(schluessel: string, aktion: () => Promise<R>) => {
    setState((current) => ({ ...current, pending: schluessel, actionError: null }));
    try {
      return await aktion();
    } catch (actionError) {
      setState((current) => ({ ...current, actionError: errorMessage(actionError) }));
      return null;
    } finally {
      setState((current) => (current.pending === schluessel ? { ...current, pending: null } : current));
    }
  }, []);

  const speichern = useCallback(
    async (input: GradeWrite) => {
      const gespeichert = await run("create", () => createGrade(input));
      if (gespeichert) {
        await abrufen();
      }
      return gespeichert;
    },
    [abrufen, run],
  );

  const loeschen = useCallback(
    async (gradeId: number) => {
      const ergebnis = await run(`delete:${gradeId}`, () => deleteGrade(gradeId).then(() => true));
      await abrufen();
      return ergebnis !== null;
    },
    [abrufen, run],
  );

  const freigabeSetzen = useCallback(
    async (enabled: boolean) => {
      const antwort = await run("sharing", () => setGradeSharing(enabled));
      if (antwort) {
        // Nur die Freigabe uebernehmen, nicht die ganze Karte neu laden: der
        // Schnitt aendert sich dadurch nicht, und ein Reload wuerde die Linie
        // fuer eine Einstellung kurz verschwinden lassen.
        setState((current) => ({ ...current, sharing: antwort }));
      }
      return antwort !== null;
    },
    [run],
  );

  const vorschau = useCallback(
    async (input: ImportInput) => {
      const ergebnis = await run("preview", () => previewGradeImport(input));
      return ergebnis;
    },
    [run],
  );

  const uebernehmen = useCallback(
    async (input: ImportInput & { keys: string[] }) => {
      const ergebnis = await run("import", () => importGrades(input));
      if (ergebnis) {
        await abrufen();
      }
      return ergebnis;
    },
    [abrufen, run],
  );

  const reload = useCallback(async () => {
    setState((current) => ({ ...current, status: "loading", error: null, actionError: null }));
    await abrufen();
  }, [abrufen]);

  return { ...state, reload, projectedWeight, setProjectedWeight: setGewicht, speichern, loeschen, freigabeSetzen, vorschau, uebernehmen };
}
