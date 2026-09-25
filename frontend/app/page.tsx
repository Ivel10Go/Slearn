"use client";

import { LoaderCircle, Sparkles } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { DashboardView } from "@/components/dashboard/dashboard-view";
import { useDashboard } from "@/lib/use-dashboard";

function AuthLoading() {
  return (
    <main className="app-surface flex min-h-screen items-center justify-center px-6">
      <div className="flex flex-col items-center gap-4 text-center">
        <span className="flex h-14 w-14 animate-pulse items-center justify-center rounded-2xl bg-primary text-primary-foreground shadow-lg shadow-primary/20">
          <Sparkles className="h-6 w-6" aria-hidden="true" />
        </span>
        <LoaderCircle className="h-5 w-5 animate-spin text-primary" aria-label="Session wird geprüft" />
      </div>
    </main>
  );
}

export default function DashboardPage() {
  const { data, user, error, status, retry } = useDashboard();

  if (status === "redirecting") {
    return <AuthLoading />;
  }

  return (
    <AppShell user={user}>
      <DashboardView data={data} error={error} isLoading={status === "loading"} onRetry={retry} username={user?.username} />
    </AppShell>
  );
}
