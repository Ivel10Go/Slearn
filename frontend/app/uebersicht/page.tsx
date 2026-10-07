"use client";

import { AppShell } from "@/components/app-shell";
import { DashboardView } from "@/components/dashboard/dashboard-view";
import { useDashboard } from "@/lib/use-dashboard";

/**
 * Die Uebersicht: Stundenplan, Hausaufgaben, Pruefungen und Termine auf einen
 * Blick. Frueher die Startseite; jetzt steht dort der Assistent, und hier
 * schaut man nach, statt zu klicken.
 */
export default function UebersichtPage() {
  const { data, user, error, status, retry } = useDashboard();

  return (
    <AppShell user={user}>
      <DashboardView data={data} error={error} isLoading={status === "loading"} onRetry={retry} username={user?.username} />
    </AppShell>
  );
}
