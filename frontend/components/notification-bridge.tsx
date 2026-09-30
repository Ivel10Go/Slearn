"use client";

import { useNotifications } from "@/lib/use-notifications";

/**
 * Die Bruecke zwischen Service Worker und Seite.
 *
 * Sie haengt in `app/providers.tsx` neben `ServiceWorkerRegister` und damit auf
 * **jeder** Route - nicht nur auf dem Dashboard. Das ist der Grund, warum die
 * Erinnerung keinen eigenen Anker und keine eigene Route bekommen hat: die
 * Lernende kann auf `/lernen`, in den Einstellungen oder im Kalender sein und
 * soll trotzdem erinnert werden. Das Dashboard zeigt nur, was zuletzt kam.
 *
 * Die Komponente rendert nichts. Sie haelt den Abruf, die Bruecke zum Worker
 * und den Zustand am Leben; alles Sichtbare steht in
 * `components/settings/notification-settings-card.tsx`.
 */
export function NotificationBridge() {
  useNotifications();
  return null;
}
