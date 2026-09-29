"use client";

import { useEffect, useState } from "react";
import { getSession } from "./api";
import type { UserResponse } from "./types";

/**
 * Das Konto fuer die Kopfzeile.
 *
 * Drei Seiten brauchen es, und `getSession` liefert `null` bei einem 401
 * statt zu werfen - deshalb ist `catch` hier nur eine Absicherung gegen
 * `503` und gegen den Demo-Pfad, nicht der Normalfall.
 *
 * Ein Fehler blockiert die Seite nicht. Die Seiten holen ihre eigentlichen
 * Daten ohnehin ueber eigene Routen; ein nicht ladender Kopf darf sie nicht
 * aufhalten.
 */
export function useSession() {
  const [user, setUser] = useState<UserResponse | null>(null);

  useEffect(() => {
    let aktiv = true;
    void getSession()
      .then((sitzung) => {
        if (aktiv) {
          setUser(sitzung);
        }
      })
      .catch(() => undefined);
    return () => {
      aktiv = false;
    };
  }, []);

  return user;
}
