"use client";

import { AppShell } from "@/components/app-shell";
import { AuthLoading } from "@/components/auth-loading";
import { SettingsView } from "@/components/settings/settings-view";
import { useIntegrations } from "@/lib/use-integrations";

export default function SettingsPage() {
  const {
    user,
    integrations,
    catalog,
    error,
    actionError,
    status,
    pendingAction,
    testResults,
    retry,
    addIntegration,
    changeIntegration,
    checkIntegration,
    removeIntegration,
    logout,
  } = useIntegrations();

  if (status === "redirecting") {
    return <AuthLoading />;
  }

  return (
    <AppShell user={user}>
      <SettingsView
        user={user}
        integrations={integrations}
        catalog={catalog}
        error={error}
        actionError={actionError}
        status={status}
        pendingAction={pendingAction}
        testResults={testResults}
        onRetry={retry}
        onAdd={addIntegration}
        onToggle={(integration, enabled) => void changeIntegration(integration.id, { enabled })}
        onTest={(integration) => void checkIntegration(integration.id)}
        onDelete={(integration) => void removeIntegration(integration.id)}
        onLogout={() => void logout()}
      />
    </AppShell>
  );
}
