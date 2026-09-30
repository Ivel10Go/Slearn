"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { AppShell } from "@/components/app-shell";
import { SettingsView } from "@/components/settings/settings-view";
import { logout } from "@/lib/api";
import { useIntegrations } from "@/lib/use-integrations";
import { useNotifications } from "@/lib/use-notifications";

export default function SettingsPage() {
  const router = useRouter();
  const [isLoggingOut, setIsLoggingOut] = useState(false);
  const notifications = useNotifications();
  const {
    user,
    integrations,
    catalog,
    writing,
    feed,
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
    authorizeIntegration,
    removeIntegration,
    toggleWriting,
    createFeed,
  } = useIntegrations();

  /**
   * Meldet ab und wechselt zur Startseite.
   *
   * `POST /auth/logout` loescht nur das Session-Cookie. Die Seiten von hier
   * laden ihre Daten ueber `GET /v1/...`, das ohne Cookie mit 401 endet -
   * ohne den Wechsel wuerde also eine Fehlermeldung statt des Lernraums
   * stehen bleiben. `router.replace` statt `push`, damit die Zuruecktaste
   * nicht auf eine abgelaufene Seite fuehrt.
   */
  async function abmelden() {
    setIsLoggingOut(true);
    try {
      await logout();
    } catch {
      // Auch bei einem Fehler wird weitergewechselt: ohne Cookie ist die
      // Sitzung ungueltig, und die Seiten wuerden ohnehin 403 bekommen.
    } finally {
      setIsLoggingOut(false);
      router.replace("/");
      router.refresh();
    }
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
        syncResults={syncResults}
writing={writing}
        feed={feed}
        notifications={notifications}
        onRetry={retry}
        onAdd={addIntegration}
        onToggle={(integration, enabled) => void changeIntegration(integration.id, { enabled })}
        onTest={(integration) => void checkIntegration(integration.id)}
        onSync={(integration) => void runSync(integration.id)}
        onAuthorize={(integration) => void authorizeIntegration(integration.id)}
        onDelete={(integration) => void removeIntegration(integration.id)}
        onToggleWriting={(enabled) => void toggleWriting(enabled)}
        onCreateFeed={() => void createFeed()}
        onLogout={() => void abmelden()}
        isLoggingOut={isLoggingOut}
      />
    </AppShell>
  );
}
