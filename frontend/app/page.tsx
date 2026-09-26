"use client";

import { AppShell } from "@/components/app-shell";
import { AuthLoading } from "@/components/auth-loading";
import { DashboardView } from "@/components/dashboard/dashboard-view";
import { useDashboard } from "@/lib/use-dashboard";

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
