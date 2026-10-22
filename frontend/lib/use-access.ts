"use client";

import { createContext, useContext } from "react";

/**
 * Ob dieser Slearn mit Anmeldung laeuft (`AUTH_ENABLED=true`).
 *
 * Die Schranke (`components/setup-gate.tsx`) weiss es nach ihrer ersten
 * Pruefung und reicht es hier weiter. Abmelden gibt es nur dann - im lokalen
 * Modus wuerde der Knopf ein Cookie loeschen, das es nie gab, und danach
 * waere man sofort wieder "angemeldet".
 */
export const ServerModeContext = createContext(false);

export function useServerMode() {
  return useContext(ServerModeContext);
}
