"use client";

import { AppShell } from "@/components/app-shell";
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
    syncResults,
    retry,
    addIntegration,
    changeIntegration,
    checkIntegration,
    runSync,
    removeIntegration,
  } = useIntegrations();

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
        syncResults={syncResults}
        onRetry={retry}
        onAdd={addIntegration}
        onToggle={(integration, enabled) => void changeIntegration(integration.id, { enabled })}
        onTest={(integration) => void checkIntegration(integration.id)}
        onSync={(integration) => void runSync(integration.id)}
        onDelete={(integration) => void removeIntegration(integration.id)}
      />
    </AppShell>
  );
}
