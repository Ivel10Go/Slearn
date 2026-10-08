"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  createPushSubscription,
  deletePushSubscription,
  getNotificationSettings,
  getPendingNotifications,
  getPushSubscriptions,
  getVapidKey,
  markNotificationsRead,
  runReminders,
  updateNotificationSettings,
  type NotificationSettingsChanges,
} from "./api";
import {
  INTERVALL_HINTERGRUND_MS,
  INTERVALL_SICHTBAR_MS,
  MAX_ABRUF,
  NACHRICHT_ANZEIGEN,
  NACHRICHT_PUSH,
  NACHRICHT_PUSH_KEY,
  ankerFuer,
  base64urlToBytes,
  darfLokalAnzeigen,
} from "./notifications";
import type {
  LernraumNotification,
  NotificationKind,
  NotificationSettings,
  PushSubscriptionRecord,
  ReminderRunReport,
  VapidKey,
} from "./types";
import { errorMessage } from "./use-collection";
import { t } from "./i18n";

type Status = "loading" | "ready" | "error";

export interface NotificationState {
  settings: NotificationSettings | null;
  vapid: VapidKey | null;
  subscriptions: PushSubscriptionRecord[];
  permission: NotificationPermission | "unsupported";
  /** Was der Browser gerade kann. Steht in den Einstellungen als Klartext. */
  pushMoeglich: boolean;
  status: Status;
  error: string | null;
  actionError: string | null;
  pending: string | null;
  /** Was der letzte Lauf gemeldet hat - in der Einstellungen sichtbar. */
  pruefbericht: ReminderRunReport | null;
  /** Die zuletzt gezeigten Erinnerungen - fuer die Kachel auf dem Dashboard. */
  zuletzt: LernraumNotification[];
}

export interface NotificationApi {
  reload: () => Promise<void>;
  speichern: (changes: NotificationSettingsChanges) => Promise<boolean>;
  erlaubnisAnfragen: () => Promise<boolean>;
  pushAnmelden: () => Promise<boolean>;
  pushAbmelden: () => Promise<boolean>;
  pruefen: () => Promise<ReminderRunReport | null>;
  quittieren: (ids: number[]) => Promise<void>;
}

const ANFANG: NotificationState = {
  settings: null,
  vapid: null,
  subscriptions: [],
  permission: "default",
  pushMoeglich: false,
  status: "loading",
  error: null,
  actionError: null,
  pending: null,
  pruefbericht: null,
  zuletzt: [],
};

function permissionVon(): NotificationPermission | "unsupported" {
  return typeof Notification === "undefined" ? "unsupported" : Notification.permission;
}

/**
 * Der ganze Benachrichtigungsweg in einem Hook: die Seite, der Worker und die
 * Einstellungen.
 *
 * Drei Entscheidungen sind hier festgeschrieben, weil sie sonst an drei Stellen
 * verschieden ausfallen:
 *
 * 1. **Lokal zuerst.** Der Hook fragt `GET /v1/notifications` und zeigt, was
 *    dort liegt - unabhaengig davon, ob ein Abo existiert. Ein Browser ohne
 *    Push-Berechtigung ist damit kein Benutzer zweiter Klasse.
 * 2. **Quittieren, nicht anzeigen und vergessen.** Eine gezeigte Zeile wird
 *    ueber `POST /read` abgehakt, damit sie beim naechsten Abruf nicht wieder
 *    kommt. Wird die Quittierung zurueckgestellt (Tab geschlossen), kommt sie
 *    wieder - besser zweimal zu oft als nie.
 * 3. **Der Worker bekommt den Schluessel, er hat ihn nicht.** Die Seite holt
 *    `/v1/notifications/vapid` und schickt ihn per `postMessage`. In
 *    `public/sw.js` steht kein Schluessel, und in einer versionierten Datei
 *    sollte auch keiner stehen.
 */
export function useNotifications(): NotificationApi & NotificationState {
  const requestId = useRef(0);
  const hoechsteId = useRef(0);
  const [state, setState] = useState<NotificationState>(ANFANG);
  const [permission, setPermission] = useState<NotificationPermission | "unsupported">("default");
  const [pushMoeglich, setPushMoeglich] = useState(false);

  const abrufen = useCallback(async () => {
    const currentRequest = ++requestId.current;
    try {
      const [settings, vapid, subscriptions] = await Promise.all([
        getNotificationSettings(),
        getVapidKey(),
        getPushSubscriptions(),
      ]);
      if (currentRequest !== requestId.current) {
        return;
      }
      setState((current) => ({
        ...current,
        settings,
        vapid,
        subscriptions,
        status: "ready",
        error: null,
      }));
    } catch (loadError) {
      if (currentRequest !== requestId.current) {
        return;
      }
      setState((current) => ({
        ...current,
        error: errorMessage(loadError, t("Die Benachrichtigungseinstellungen konnten nicht geladen werden.")),
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
      requestId.current += 1;
    };
  }, [abrufen]);

  useEffect(() => {
    setPermission(permissionVon());
    setPushMoeglich(
      typeof window !== "undefined" &&
        "serviceWorker" in navigator &&
        typeof window.PushManager === "function" &&
        typeof window.Notification !== "undefined",
    );
  }, []);

  // Der Schluessel wandert zum Worker - und die Seite fragt ihn auch wieder ab,
  // falls ein zweiter Tab ihn gemeldet hat.
  useEffect(() => {
    const schluessel = state.vapid?.public_key ?? null;
    if (!("serviceWorker" in navigator)) {
      return;
    }
    let aufraeumen: (() => void) | undefined;
    const melden = async () => {
      try {
        const registration = await navigator.serviceWorker.ready;
        registration.active?.postMessage({ type: NACHRICHT_PUSH_KEY, publicKey: schluessel });
        aufraeumen = meldungHolen(registration.active);
      } catch {
        // Kein registrierter Worker: die Erinnerungen laufen dann ueber den
        // lokalen Weg der Seite, und das ist kein Fehler.
      }
    };
    void melden();
    return () => aufraeumen?.();
  }, [state.vapid?.public_key]);

  /**
   * Eine Erinnerung anzeigen - aber nur, wenn der Browser zustimmt und der Tab
   * sichtbar ist. Im Hintergrund uebernimmt der Worker, sonst wuerde die
   * Benachrichtigung im Nirgends verschwinden.
   */
  const zeigen = useCallback(async (erinnerung: LernraumNotification) => {
    if (typeof Notification === "undefined") {
      return false;
    }
    if (!darfLokalAnzeigen(Notification.permission, document.hidden)) {
      // Der Worker zeigt sie als Systemmeldung - auch wenn dieser Tab gerade
      // nichts anzeigen kann.
      try {
        const registration = await navigator.serviceWorker?.ready;
        registration?.active?.postMessage({ type: NACHRICHT_ANZEIGEN, payload: erinnerung });
      } catch {
        // Ohne Worker gibt es keinen zweiten Weg. Die Zeile bleibt ungelesen und
        // kommt beim naechsten Abruf wieder.
      }
      return false;
    }
    try {
      new Notification(erinnerung.title, { body: erinnerung.body, tag: `${erinnerung.kind}:${erinnerung.target_id}` });
      return true;
    } catch {
      return false;
    }
  }, []);

  const abruf = useCallback(async () => {
    try {
      const offen = await getPendingNotifications(hoechsteId.current || undefined);
      if (offen.length === 0) {
        return;
      }
      const gezeigt = await Promise.all(offen.map((erinnerung) => zeigen(erinnerung)));
      const quittierbar = offen.filter((_erinnerung, index) => gezeigt[index]);
      if (quittierbar.length > 0) {
        await markNotificationsRead(quittierbar.map((erinnerung) => erinnerung.id));
      }
      for (const erinnerung of offen) {
        hoechsteId.current = Math.max(hoechsteId.current, erinnerung.id);
      }
      setState((current) => ({ ...current, zuletzt: [...offen, ...current.zuletzt].slice(0, 20) }));
    } catch {
      // Ein Fehler beim Abruf ist kein Grund, etwas zu melden: der naechste
      // Versuch kommt von selbst, und der Benutzer kann unterwegs offline sein.
    }
  }, [zeigen]);

  useEffect(() => {
    const planen = () => {
      window.clearTimeout(timer);
      const verzoegert = document.hidden ? INTERVALL_HINTERGRUND_MS : INTERVALL_SICHTBAR_MS;
      timer = window.setTimeout(() => void abruf(), verzoegert);
    };
    let timer = window.setTimeout(() => void abruf(), 0);
    document.addEventListener("visibilitychange", planen);
    return () => {
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", planen);
    };
  }, [abruf]);

  // Eine Push-Nachricht, die der Worker nur weitergereicht hat. Sie wird wie
  // jede andere Erinnerung behandelt - angezeigt, quittiert, verlinkt.
  useEffect(() => {
    if (!("serviceWorker" in navigator)) {
      return;
    }
    let aufraeumen: (() => void) | undefined;
    const einrichten = async () => {
      try {
        const registration = await navigator.serviceWorker.ready;
        aufraeumen = meldungHolen(registration.active);
      } catch {
        // Ohne Worker gibt es keine Push-Nachrichten. Unkritisch.
      }
    };
    void einrichten();
    return () => aufraeumen?.();
  }, []);

  const speichern = useCallback(async (changes: NotificationSettingsChanges) => {
    setState((current) => ({ ...current, pending: "speichern", actionError: null }));
    try {
      const gespeichert = await updateNotificationSettings(changes);
      setState((current) => ({ ...current, settings: gespeichert }));
      return true;
    } catch (actionError) {
      setState((current) => ({
        ...current,
        actionError: errorMessage(actionError, t("Die Einstellungen konnten nicht gespeichert werden.")),
      }));
      return false;
    } finally {
      setState((current) => (current.pending === "speichern" ? { ...current, pending: null } : current));
    }
  }, []);

  const erlaubnisAnfragen = useCallback(async () => {
    if (typeof Notification === "undefined") {
      return false;
    }
    try {
      const erlaubnis = await Notification.requestPermission();
      setPermission(erlaubnis);
      return erlaubnis === "granted";
    } catch {
      return false;
    }
  }, []);

  const pushAnmelden = useCallback(async () => {
    setState((current) => ({ ...current, pending: "push", actionError: null }));
    try {
      if (!("serviceWorker" in navigator) || typeof window.PushManager !== "function") {
        throw new Error(t("Dieser Browser kann keine Push-Abos anlegen."));
      }
      const vapid = await getVapidKey();
      if (!vapid.configured || !vapid.public_key) {
        // Kein Schluesselpaar auf dem Server: das ist kein Fehler, sondern die
        // Konfiguration, in der der lokale Weg arbeitet.
        throw new Error(t("Auf diesem Server ist Web-Push nicht eingerichtet."));
      }
      const registration = await navigator.serviceWorker.ready;
      registration.active?.postMessage({ type: NACHRICHT_PUSH_KEY, publicKey: vapid.public_key });
      const abo = await registration.pushManager.getSubscription();
      const erstellt =
        abo ??
        (await registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: base64urlToBytes(vapid.public_key) as BufferSource,
        }));
      const json = erstellt.toJSON();
      if (!json.endpoint || !json.keys?.p256dh || !json.keys.auth) {
        throw new Error(t("Der Browser hat kein vollständiges Abonnement geliefert."));
      }
      const gespeichert = await createPushSubscription({
        endpoint: json.endpoint,
        p256dh: json.keys.p256dh,
        auth: json.keys.auth,
      });
      setState((current) => ({ ...current, vapid, subscriptions: [...state.subscriptions, gespeichert] }));
      return true;
    } catch (actionError) {
      setState((current) => ({
        ...current,
        actionError: errorMessage(actionError, t("Das Endgerät konnte nicht angemeldet werden.")),
      }));
      return false;
    } finally {
      setState((current) => (current.pending === "push" ? { ...current, pending: null } : current));
    }
  }, [state.subscriptions]);

  const pushAbmelden = useCallback(async () => {
    setState((current) => ({ ...current, pending: "push", actionError: null }));
    try {
      if ("serviceWorker" in navigator) {
        const registration = await navigator.serviceWorker.ready;
        const abo = await registration.pushManager.getSubscription();
        if (abo) {
          // Erst das Abo im Browser loeschen, dann die Zeile. Umgekehrt bliebe
          // eine Zeile, an die der Browser nie wieder etwas schickt.
          await abo.unsubscribe();
        }
      }
      for (const eintrag of state.subscriptions) {
        await deletePushSubscription(eintrag.id);
      }
      setState((current) => ({ ...current, subscriptions: [] }));
      return true;
    } catch (actionError) {
      setState((current) => ({
        ...current,
        actionError: errorMessage(actionError, t("Das Endgerät konnte nicht abgemeldet werden.")),
      }));
      return false;
    } finally {
      setState((current) => (current.pending === "push" ? { ...current, pending: null } : current));
    }
  }, [state.subscriptions]);

  const pruefen = useCallback(async () => {
    setState((current) => ({ ...current, pending: "pruefen", actionError: null }));
    try {
      const bericht = await runReminders();
      setState((current) => ({ ...current, pruefbericht: bericht }));
      // Was der Lauf erzeugt hat, direkt holen: der Knopf soll etwas zeigen und
      // nicht erst beim naechsten Abruf.
      hoechsteId.current = 0;
      await abruf();
      return bericht;
    } catch (actionError) {
      setState((current) => ({
        ...current,
        actionError: errorMessage(actionError, t("Die Erinnerungen konnten nicht geprüft werden.")),
      }));
      return null;
    } finally {
      setState((current) => (current.pending === "pruefen" ? { ...current, pending: null } : current));
    }
  }, [abruf]);

  const quittieren = useCallback(async (ids: number[]) => {
    if (ids.length === 0) {
      return;
    }
    try {
      await markNotificationsRead(ids);
      setState((current) => ({ ...current, zuletzt: current.zuletzt.filter((erinnerung) => !ids.includes(erinnerung.id)) }));
    } catch {
      // Bleibt liegen und kommt wieder - das ist der harmlosere Fehler.
    }
  }, []);

  const reload = useCallback(async () => {
    setState((current) => ({ ...current, status: "loading", error: null, actionError: null }));
    await abrufen();
  }, [abrufen]);

  return {
    ...state,
    permission,
    pushMoeglich,
    reload,
    speichern,
    erlaubnisAnfragen,
    pushAnmelden,
    pushAbmelden,
    pruefen,
    quittieren,
  };
}

/**
 * Die Nachrichten eines Workers abhoeren.
 *
 * Der `message`-Handler wird genau einmal pro Seite gesetzt und raeumt sich beim
 * Unmount ab - sonst haette nach jedem Routenwechsel eine alte Bruecke
 * zugehoert.
 */
function meldungHolen(worker: ServiceWorker | null): () => void {
  if (!worker || typeof worker.addEventListener !== "function") {
    return () => undefined;
  }
  // Die Typen von `lib.dom` kennen `message` beim `ServiceWorker` nicht, obwohl
  // jeder Browser es hat. Deshalb der Umweg ueber `EventListener` mit einem
  // `instanceof`-Test - kein `any`, sonst waere die Lint-Baseline hier auch
  // betroffen.
  const handler: EventListener = (ereignis) => {
    if (!(ereignis instanceof MessageEvent)) {
      return;
    }
    const daten = ereignis.data as { type?: string; payload?: { url?: string; kind?: NotificationKind; navigate?: boolean } } | null;
    if (!daten || daten.type !== NACHRICHT_PUSH) {
      return;
    }
    // Der Anker kommt aus der Nutzlast; der Kanal dient als Rueckfall, weil
    // `payload.kind` in einer aelteren Fassung des Workers fehlen darf.
    const ziel = daten.payload?.url ?? (daten.payload?.kind ? ankerFuer(daten.payload.kind) : "/");
    if (daten.payload?.navigate && typeof ziel === "string" && ziel.startsWith("/")) {
      // Ein Klick auf eine Benachrichtigung: der offene Tab springt zum Anker.
      window.location.hash = ziel.includes("#") ? ziel.slice(ziel.indexOf("#")) : "";
    }
  };
  worker.addEventListener("message", handler);
  return () => worker.removeEventListener("message", handler);
}

/** Wie viele Erinnerungen ein Abruf holt - als Konstante fuer die Oberflaeche. */
export { MAX_ABRUF };
