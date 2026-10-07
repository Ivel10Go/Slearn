"use client";

import { AppShell } from "@/components/app-shell";
import { AssistantView } from "@/components/assistant/assistant-view";
import { useDashboard } from "@/lib/use-dashboard";

/**
 * Die Startseite ist der Assistent. Man sagt, was ansteht, und er traegt ein,
 * plant die Woche und hakt ab - statt dass man sich durch Karten klickt. Die
 * Tagesdaten liefern nur den Ueberblick oben und die Vorschlaege; das Gespraech
 * selbst funktioniert auch, wenn sie nicht laden.
 */
export default function StartPage() {
  const { data, user, retry } = useDashboard();

  return (
    <AppShell user={user}>
      <AssistantView data={data} username={user?.username} onGeaendert={retry} />
    </AppShell>
  );
}
