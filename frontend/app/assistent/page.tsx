"use client";

import { AppShell } from "@/components/app-shell";
import { AssistantView } from "@/components/assistant/assistant-view";
import { useSession } from "@/lib/use-session";

/**
 * Der Lern-Assistent. Frueher stand hier nur „Frag deine Unterlagen“ als Karte
 * im Dashboard und auf /dokumente; jetzt ist es ein Gespraech mit Gedaechtnis,
 * Sprache und Eintraegen.
 */
export default function AssistentPage() {
  const user = useSession();
  return (
    <AppShell user={user}>
      <AssistantView />
    </AppShell>
  );
}
