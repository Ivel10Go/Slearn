"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getRewards } from "./api";
import type { RewardOverview } from "./types";
import { errorMessage } from "./use-collection";

type RewardsStatus = "loading" | "ready" | "error";

export interface RewardsState {
  overview: RewardOverview | null;
  status: RewardsStatus;
  /** Fehler des Ladevorgangs. */
  error: string | null;
}

export interface RewardsApi {
  reload: () => Promise<void>;
}

const ANFANG: RewardsState = { overview: null, status: "loading", error: null };

/**
 * Punkte, Auszeichnungen und Buchungen in einem Hook.
 *
 * **Eine** Anfrage, nicht drei. Die Karte zeigt alles drei fuer denselben Gegenstand:
 * eine Punktzahl ohne die Buchung, aus der sie stammt, waere eine Behauptung, und
 * drei Endpunkte wuerden drei Moeglichkeiten schaffen, in denen die Karte einen
 * halben Stand zeigt. Das ist dieselbe Begruendung, mit der auch `useProgress` zwei
 * Getter **parallel** in einem Hook aufruft, statt sie auf zwei Karten zu verteilen.
 *
 * **Kein Demo-Fallback.** Die Lerndaten haben auch keinen: eine erfundene Punktzahl
 * waere an sich nicht als solche erkennbar - sie wuerde einfach dastehen. Eine
 * erfundene Lernzeit erkennt man dagegen an ihren Zahlen.
 *
 * `refreshKey` laesst die Kachel von aussen neu laden. Die Fortschrittskarte haengt
 * am selben Zaehler, und das ist Absicht: **ein** Ereignis (ein beendeter Fokusblock)
 * aendert Lernzeit *und* Punkte. Ein zweiter Zaehler koennte nur etwas anderes als
 * dasselbe Ereignis beobachten - und damit entweder zu frueh oder zu spaet
 * aktualisieren. Es gibt hier keinen Ereignisbus, nur diesen Zaehler; wer beim Lesen
 * wissen will, wer die Anzeige aktualisiert, sieht es an dieser einen Stelle.
 *
 * Nach einer Aktion wird hier nichts gepatcht: die Karte hat keine eigene Schreibseite.
 * Punkte entstehen beim Abschluss einer Aufgabe, und das ist ein Ereignis, das die
 * aufrufende Karte ohnehin kennt - sie zaehlt es fuer die Fortschrittskarte und fuer
 * diese gleich mit.
 */
export function useRewards(refreshKey = 0): RewardsApi & RewardsState {
  const requestId = useRef(0);
  const [state, setState] = useState<RewardsState>(ANFANG);

  const abrufen = useCallback(async () => {
    const currentRequest = ++requestId.current;
    try {
      const overview = await getRewards();
      if (currentRequest !== requestId.current) {
        return;
      }
      setState((current) => ({ ...current, overview, error: null, status: "ready" }));
    } catch (loadError) {
      if (currentRequest !== requestId.current) {
        return;
      }
      setState((current) => ({
        ...current,
        error: errorMessage(loadError, "Die Belohnungen konnten nicht geladen werden."),
        status: "error",
      }));
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

  const reload = useCallback(async () => {
    setState((current) => ({ ...current, status: "loading", error: null }));
    await abrufen();
  }, [abrufen]);

  return { ...state, reload };
}
