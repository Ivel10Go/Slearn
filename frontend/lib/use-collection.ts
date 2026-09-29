"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./api";

type CollectionStatus = "loading" | "ready" | "error";

/** Alles, was eine CRUD-Liste fuer ihre Oberflaeche braucht. */
export interface Collection<T> {
  items: T[];
  status: CollectionStatus;
  /** Fehler des Ladevorgangs. Bleibt bestehen, auch wenn eine Aktion scheitert. */
  error: string | null;
  /** Aktionsschluessel der laufenden Operation, z. B. ``create`` oder ``delete:7``. */
  pending: string | null;
  /** Meldung der letzten fehlgeschlagenen Aktion, getrennt vom Ladefehler. */
  actionError: string | null;
}

export interface CollectionApi<T, TWrite, TChanges> extends Collection<T> {
  reload: () => Promise<void>;
  create: (input: TWrite) => Promise<T | null>;
  update: (id: number, changes: TChanges) => Promise<T | null>;
  remove: (id: number) => Promise<boolean>;
  /**
   * Nimmt ein vom Server gelesenes Objekt in die Liste auf.
   *
   * Noetig fuer Aktionen, die kein `PATCH` auf die Sammlung sind - etwa das
   * Abschicken einer Hausaufgabe ueber `/homework/{id}/completed`. Ohne `patch`
   * gaebe es nur zwei schlechte Optionen: die ganze Liste neu laden (die
   * Reihenfolge springt) oder den Zustand per Hand nachziehen (zwei Stellen,
   * die auseinanderlaufen).
   */
  patch: (item: T) => void;
  /**
   * Fuehrt eine eigene Aktion aus, die ein Element dieser Sammlung veraendert,
   * und uebernimmt deren Ergebnis. `update` ist der Sonderfall, bei dem Aktion
   * und `changes` zusammenpassen; hier kommt der Pfad von aussen.
   */
  mutate: (key: string, action: () => Promise<T>) => Promise<T | null>;
}

export function errorMessage(error: unknown, fallback = "Die Aktion ist fehlgeschlagen. Bitte versuche es erneut.") {
  if (error instanceof ApiError) {
    // 401 bedeutet in diesem Stack fast immer: die Session ist weg. Die
    // konkrete Backend-Meldung ("Not authenticated") hilft der Lernenden dann
    // weniger als der Hinweis, sich neu anzumelden.
    return error.status === 401 ? "Die Anmeldung ist abgelaufen. Bitte melde dich erneut an." : error.message;
  }
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return fallback;
}

/**
 * Der Ersatz fuer `id === x ? neu : alt` an drei Stellen im Code.
 *
 * Nach einem Schreibvorgang kommt das frisch gelesene Objekt vom Backend
 * zurueck. `mergeById` ersetzt genau dieses Element und laesst die Reihenfolge
 * der Liste unangetastet - ein Neuladen wuerde sie umstellen, weil die
 * Sortierung des Backends (`ORDER BY due_date`) bei einer lokalen Einfuegung
 * noch gar nicht greift.
 */
export function mergeById<T extends { id: number }>(items: T[], updated: T): T[] {
  const index = items.findIndex((item) => item.id === updated.id);
  if (index === -1) {
    return [...items, updated];
  }
  const next = items.slice();
  next[index] = updated;
  return next;
}

/** Haengt ein neu angelegtes Element an - oder faegt es nach `sort` ein. */
export function appendSorted<T extends { id: number }>(items: T[], created: T, sort: (a: T, b: T) => number): T[] {
  return [...items, created].sort(sort);
}

/**
 * Eine CRUD-Liste ueber eine beliebige Sammlung.
 *
 * Bewusst ein Hook statt einer Komponente: Hausaufgaben, Pruefungen,
 * Kalendereintraege, Lernaufgaben und Dokumente brauchen alle dieselben
 * Operationen mit derselben Fehler- und Ladebehandlung. Fuenf Kopien davon
 * waeren fuenf Stellen, an denen ein 401-Handler fehlen koennte.
 *
 * Der Zustand liegt in **einem** Objekt, nicht in fuenf `useState`-Aufrufen.
 * Das ist hier nicht Geschmack, sondern Voraussetzung: `react-hooks/exhaustive-deps`
 * und der React Compiler verstehen nur die Objektform, und bei fuenf
 * getrennten Zustaenden koennte ein Zwischenstand sichtbar werden, in dem
 * `pending` schon geleert, `actionError` aber noch nicht gesetzt ist.
 *
 * Nach jeder Aktion wird **lokal** gepatcht, nicht neu geladen. Das haelt die
 * Oberflaeche ohne Ladeblinken und bewahrt die Reihenfolge der Liste.
 *
 * `actions` darf fehlen, wenn eine Sektion nur liest - die Lernaufgaben-Kachel
 * etwa braucht die Liste der Lernplaene fuer den Generator der Pruefungen,
 * legt sie aber nirgends an. Ohne diese Ausnahme muesste jede read-only
 * Sektion drei Attrappen bereitstellen, die nie aufgerufen werden.
 */
export function useCollection<T extends { id: number }, TWrite, TChanges>(
  list: () => Promise<T[]>,
  actions: {
    create: (input: TWrite) => Promise<T>;
    update: (id: number, changes: TChanges) => Promise<T>;
    remove: (id: number) => Promise<void>;
  },
  options: { sort?: (a: T, b: T) => number; refreshKey?: number } = {},
): CollectionApi<T, TWrite, TChanges> {
  const requestId = useRef(0);
  const [state, setState] = useState<Collection<T>>({ items: [], status: "loading", error: null, pending: null, actionError: null });

  // `list` und `actions` sind bei jedem Render neue Objekte. Ohne diese
  // Refs wuerde der Lade-Effekt bei jeder Renderrunde erneut feuern und die
  // Liste im Kreis aktualisieren.
  //
  // Die Refs werden bewusst in einem Effekt gefuehrt und **nicht** direkt
  // beim Rendern: der React-Compiler verbietet Ref-Zugriffe in der
  // Renderphase. Dieser Effekt steht bewusst **vor** dem Lade-Effekt - beide
  // laufen beim Mount in dieser Reihenfolge, deshalb greift `listRef` beim
  // ersten Abruf schon.
  const listRef = useRef(list);
  const actionsRef = useRef(actions);
  const sortRef = useRef(options.sort);

  useEffect(() => {
    listRef.current = list;
    actionsRef.current = actions;
    sortRef.current = options.sort;
  });

  // Holt die Liste, ohne den Ladezustand zu setzen. Der Anfangszustand ist
  // bereits "loading", deshalb reicht das fuer den ersten Abruf.
  const abrufen = useCallback(async () => {
    const currentRequest = ++requestId.current;
    try {
      const loaded = await listRef.current();
      if (currentRequest !== requestId.current) {
        return;
      }
      setState((current) => ({ ...current, items: loaded, error: null, status: "ready" }));
    } catch (loadError) {
      if (currentRequest !== requestId.current) {
        return;
      }
      setState((current) => ({
        ...current,
        error: errorMessage(loadError, "Die Liste konnte nicht geladen werden."),
        status: "error",
      }));
    }
  }, []);

  useEffect(() => {
    // `setTimeout(…, 0)`: `react-hooks/set-state-in-effect` ist eine
    // Error-Regel in `eslint-config-next` und akzeptiert keinen synchronen
    // Zustandswechsel im Effektkoerper. So machen es `use-greeting.ts` und
    // `use-now.ts` ebenfalls. Praktisch ist das ein Tick Verzoegerung, in dem
    // ohnehin noch nichts zu sehen ist.
    const timer = window.setTimeout(() => void abrufen(), 0);
    return () => {
      window.clearTimeout(timer);
      // Eine spaetere Antwort darf nach dem Unmount nichts mehr setzen.
      requestId.current += 1;
    };
    // `refreshKey` gehoert hierher, damit eine Aktion in einer **anderen**
    // Sammlung diese neu laden kann - etwa wenn ein Lernplan geloescht wurde
    // und die Aufgabenliste ihre Waisen nicht mehr zeigen darf.
  }, [abrufen, options.refreshKey]);

  /**
   * Fuehrt eine Schreibaktion aus und haelt `pending` fuer die Dauer.
   *
   * `pending` ist bewusst ein einzelner Schluessel statt eines Mengen: zwei
   * gleichzeitige Loeschvorgaenge wuerden zwei Spinner zeigen, die niemand
   * unterscheiden kann. Die Oberflaeche sperrt ihre Knoepfe waehrenddessen.
   */
  const run = useCallback(
    async <R,>(key: string, action: () => Promise<R>, onSuccess: (result: R) => void): Promise<R | null> => {
      setState((current) => ({ ...current, pending: key, actionError: null }));
      try {
        const result = await action();
        onSuccess(result);
        return result;
      } catch (actionFailure) {
        setState((current) => ({ ...current, actionError: errorMessage(actionFailure) }));
        return null;
      } finally {
        // Nur zuruecksetzen, wenn noch dieselbe Aktion laeuft: ein spaeter
        // gestarteter Vorgang hat seinen eigenen Schluessel gesetzt.
        setState((current) => (current.pending === key ? { ...current, pending: null } : current));
      }
    },
    [],
  );

  const create = useCallback(
    async (input: TWrite) => {
      const aktionen = actionsRef.current;
      if (!aktionen) {
        setState((current) => ({ ...current, actionError: errorMessage(null, "Dieser Bereich lässt sich hier nicht anlegen.") }));
        return null;
      }
      return run(
        "create",
        () => aktionen.create(input),
        (created) => {
          setState((current) => {
            const sort = sortRef.current;
            return { ...current, items: sort ? appendSorted(current.items, created, sort) : [...current.items, created] };
          });
        },
      );
    },
    [run],
  );

  const update = useCallback(
    async (id: number, changes: TChanges) => {
      const aktionen = actionsRef.current;
      if (!aktionen) {
        setState((current) => ({ ...current, actionError: errorMessage(null, "Dieser Bereich lässt sich hier nicht ändern.") }));
        return null;
      }
      return run(`update:${id}`, () => aktionen.update(id, changes), (updated) => {
        setState((current) => ({ ...current, items: mergeById(current.items, updated) }));
      });
    },
    [run],
  );

  const remove = useCallback(
    async (id: number) => {
      const aktionen = actionsRef.current;
      if (!aktionen) {
        setState((current) => ({ ...current, actionError: errorMessage(null, "Dieser Bereich lässt sich hier nicht löschen.") }));
        return false;
      }
      const result = await run(
        `delete:${id}`,
        () => aktionen.remove(id),
        () => {
          setState((current) => ({ ...current, items: current.items.filter((item) => item.id !== id) }));
        },
      );
      return result !== null;
    },
    [run],
  );

  const patch = useCallback((item: T) => {
    setState((current) => ({ ...current, items: mergeById(current.items, item) }));
  }, []);

  const mutate = useCallback(
    (key: string, action: () => Promise<T>) =>
      run(key, action, (updated) => {
        setState((current) => ({ ...current, items: mergeById(current.items, updated) }));
      }),
    [run],
  );

  /** Nur aus Ereignishandlern aufrufen - hier darf der Zustand sofort wechseln. */
  const reload = useCallback(async () => {
    setState((current) => ({ ...current, status: "loading", error: null, actionError: null }));
    await abrufen();
  }, [abrufen]);

  return { ...state, reload, create, update, remove, patch, mutate };
}

export interface ReadOnlyList<T> {
  items: T[];
  status: CollectionStatus;
  error: string | null;
  reload: () => Promise<void>;
}

/**
 * Eine Liste, die nur gelesen wird.
 *
 * `useCollection` verlangt Schreibaktionen, weil es sie lokal zupflaegt. Fuer
 * eine reine Auswahl - die Fächer in einem Picker, die Lernpläne neben einem
 * Generator - gibt es nichts zu schreiben. Ohne diesen Hook muesste man dort
 * vier Funktionen hinstellen, die einen Fehler werfen: sichtbarer Ballast an
 * zwei Stellen und eine Einladung, sie doch einmal aufzurufen.
 */
export function useList<T>(list: () => Promise<T[]>, options: { refreshKey?: number } = {}): ReadOnlyList<T> {
  const [state, setState] = useState<{ items: T[]; status: CollectionStatus; error: string | null }>({
    items: [],
    status: "loading",
    error: null,
  });
  const requestId = useRef(0);
  const listRef = useRef(list);

  useEffect(() => {
    listRef.current = list;
  });

  const abrufen = useCallback(async () => {
    const currentRequest = ++requestId.current;
    try {
      setState({ items: await listRef.current(), status: "ready", error: null });
    } catch (loadError) {
      if (currentRequest !== requestId.current) {
        return;
      }
      setState({ items: [], status: "error", error: errorMessage(loadError, "Die Liste konnte nicht geladen werden.") });
    }
  }, []);

  useEffect(() => {
    // Siehe `useCollection`: `setTimeout(…, 0)` umgeht
    // `react-hooks/set-state-in-effect`.
    const timer = window.setTimeout(() => void abrufen(), 0);
    return () => {
      window.clearTimeout(timer);
      requestId.current += 1;
    };
  }, [abrufen, options.refreshKey]);

  const reload = useCallback(async () => {
    setState((current) => ({ ...current, status: "loading", error: null }));
    await abrufen();
  }, [abrufen]);

  return { ...state, reload };
}

/**
 * Eine einzelne Aktion, deren Ergebnis die Oberflaeche anzeigen will.
 *
 * Anders als `useCollection` gibt es hier keine Liste, nur einen Aufruf - ein
 * Datei-Upload, eine Frage an den Assistenten, eine Volltextsuche. Das
 * Ergebnis wird trotzdem gehalten, weil alle drei es brauchen: die Antwort
 * mit ihren Quellen, die Trefferliste, der Name der hochgeladenen Datei.
 *
 * Ein Aufruf, der fehlschlaegt, loescht das alte Ergebnis bewusst **nicht** -
 * sonst verschwande beim naechsten Versuch die Antwort, die noch da steht.
 */
export function useAction<R>() {
  const [state, setState] = useState<{ result: R | null; pending: boolean; error: string | null }>({
    result: null,
    pending: false,
    error: null,
  });

  const reset = useCallback(() => {
    setState((current) => ({ ...current, result: null, error: null }));
  }, []);

  const run = useCallback(async (action: () => Promise<R>): Promise<R | null> => {
    setState((current) => ({ ...current, pending: true, error: null }));
    try {
      const result = await action();
      setState({ result, pending: false, error: null });
      return result;
    } catch (actionError) {
      setState((current) => ({ ...current, pending: false, error: errorMessage(actionError) }));
      return null;
    }
  }, []);

  return { ...state, run, reset };
}
