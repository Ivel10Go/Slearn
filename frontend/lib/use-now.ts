"use client";

import { useEffect, useState } from "react";

/** Sekundentakt für alle Anzeigen, die "jetzt" brauchen. Liefert erst nach dem Mount eine Zeit. */
export function useNow() {
  const [now, setNow] = useState<Date | null>(null);

  useEffect(() => {
    const initialUpdate = window.setTimeout(() => setNow(new Date()), 0);
    const interval = window.setInterval(() => setNow(new Date()), 1000);
    return () => {
      window.clearTimeout(initialUpdate);
      window.clearInterval(interval);
    };
  }, []);

  return now;
}
