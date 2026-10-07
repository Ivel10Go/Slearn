"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/**
 * Der Assistent ist die Startseite. Alte Links (Lesezeichen, Benachrichtigungen,
 * `/assistent?frage=…`) landen dort - die Frage wird mitgenommen.
 */
export default function AssistentWeiterleitung() {
  const router = useRouter();
  useEffect(() => {
    router.replace(`/${window.location.search}`);
  }, [router]);
  return null;
}
