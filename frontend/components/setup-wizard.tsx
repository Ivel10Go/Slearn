"use client";

import { useState } from "react";
import Link from "next/link";
import { ArrowLeft, ArrowRight, Check } from "lucide-react";
import { BrandMark } from "@/components/app-shell";
import { RecommendedModelsCard } from "@/components/settings/recommended-models-card";
import { IntegrationsCard } from "@/components/settings/settings-view";
import { Button, buttonVariants } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useIntegrations } from "@/lib/use-integrations";
import { cn } from "@/lib/utils";
import { t } from "@/lib/i18n";

/** Die Arten je Schritt - dieselben Kinds wie im Integrationskatalog des Backends. */
const STUNDENPLAN_KINDS = ["webuntis"];
const UNTERLAGEN_KINDS = ["webdav", "documents", "ical", "google_calendar", "caldav"];

const SCHRITTE = [
  {
    titel: t("KI auf diesem Rechner"),
    text: t("Slearn hat geprüft, was dein Rechner schafft. Die Downloads laufen im Hintergrund weiter, du kannst schon zum nächsten Schritt."),
  },
  {
    titel: t("Stundenplan"),
    text: t("Verbinde WebUntis, dann kommen Stundenplan, Fächer und Ausfälle von selbst. Ohne WebUntis kannst du den Plan später aus einem Foto oder PDF übernehmen."),
  },
  {
    titel: t("Unterlagen und Kalender"),
    text: t("Ein Cloud-Ordner (z. B. Nextcloud) oder ein Ordner auf dem Rechner wird regelmäßig eingelesen, neue Dateien ordnet die KI selbst ein. Kalender holen Termine dazu."),
  },
  {
    titel: t("Fertig"),
    text: t("Alles Weitere findest du jederzeit unter Einstellungen. Dort kannst du diese Einrichtung auch noch einmal durchgehen."),
  },
] as const;

/**
 * Einrichtung nach dem ersten Start: alles einmal festlegen, statt es später in
 * den Einstellungen zu suchen. Jeder Schritt ist überspringbar und benutzt
 * dieselben Karten wie `/ki` und `/settings` - keine zweite Fassung der Formulare.
 */
export function SetupWizard() {
  const [schritt, setSchritt] = useState(0);
  const letzter = schritt === SCHRITTE.length - 1;

  return (
    <main className="min-h-screen bg-background">
      <div className="app-drag h-3" aria-hidden="true" />
      <div className="mx-auto w-full max-w-3xl px-4 pb-16 pt-6 sm:px-6">
        <div className="flex items-center gap-2">
          <BrandMark />
          <span className="text-[15px] font-semibold tracking-tight">{t("Slearn einrichten")}</span>
        </div>

        <ol className="mt-6 flex gap-1.5" aria-label={t("Fortschritt der Einrichtung")}>
          {SCHRITTE.map((eintrag, index) => (
            <li
              key={eintrag.titel}
              aria-current={index === schritt ? "step" : undefined}
              className={cn("h-1 flex-1 rounded-full", index <= schritt ? "bg-foreground" : "bg-border")}
            >
              <span className="sr-only">{eintrag.titel}</span>
            </li>
          ))}
        </ol>

        <p className="mt-6 text-[13px] text-muted-foreground">
          {t("Schritt {n} von {gesamt}", { n: schritt + 1, gesamt: SCHRITTE.length })}
        </p>
        <h1 className="mt-1 text-[22px] font-semibold">{SCHRITTE[schritt].titel}</h1>
        <p className="mt-1 max-w-2xl text-[15px] leading-6 text-muted-foreground">{SCHRITTE[schritt].text}</p>

        <div className="mt-6">
          {schritt === 0 ? <RecommendedModelsCard /> : null}
          {schritt === 1 ? <Verbindungen kinds={STUNDENPLAN_KINDS} titel={t("Stundenplan")} /> : null}
          {schritt === 2 ? <Verbindungen kinds={UNTERLAGEN_KINDS} titel={t("Unterlagen und Kalender")} /> : null}
          {letzter ? <Check className="h-10 w-10" aria-hidden="true" /> : null}
        </div>

        <div className="mt-8 flex flex-wrap items-center justify-between gap-3">
          <Button type="button" variant="ghost" onClick={() => setSchritt(schritt - 1)} disabled={schritt === 0}>
            <ArrowLeft aria-hidden="true" />
            {t("Zurück")}
          </Button>
          {letzter ? (
            <Link href="/" className={buttonVariants()}>
              {t("Zur Übersicht")}
              <ArrowRight aria-hidden="true" />
            </Link>
          ) : (
            <div className="flex gap-2">
              <Button type="button" variant="ghost" onClick={() => setSchritt(schritt + 1)}>
                {t("Überspringen")}
              </Button>
              <Button type="button" onClick={() => setSchritt(schritt + 1)}>
                {t("Weiter")}
                <ArrowRight aria-hidden="true" />
              </Button>
            </div>
          )}
        </div>
      </div>
    </main>
  );
}

/** Die Verbindungskarte der Einstellungen, auf die Arten eines Schritts beschränkt. */
function Verbindungen({ kinds, titel }: { kinds: string[]; titel: string }) {
  const ki = useIntegrations();
  const verbindungen = ki.integrations.filter((integration) => kinds.includes(integration.kind));
  const katalog = ki.catalog.filter((entry) => kinds.includes(entry.kind));

  if (ki.status === "loading" && ki.catalog.length === 0) {
    return <Skeleton className="h-48 w-full rounded-md" />;
  }
  return (
    <div className="space-y-3">
      {ki.error ? (
        <p className="text-[15px] text-destructive" role="alert">
          {ki.error}
        </p>
      ) : null}
      {ki.actionError ? (
        <p className="text-[15px] text-destructive" role="alert">
          {ki.actionError}
        </p>
      ) : null}
      <IntegrationsCard
        title={titel}
        description={titel}
        emptyTitle={t("Noch nichts verbunden")}
        emptyDescription={t("Unter „Hinzufügen“ wählst du aus, was du verbinden möchtest.")}
        integrations={verbindungen}
        catalog={katalog}
        runtime={ki.runtime}
        pendingAction={ki.pendingAction}
        testResults={ki.testResults}
        syncResults={ki.syncResults}
        isRefreshing={ki.status === "loading"}
        canWriteRemotely={ki.writing?.share_calendar_for_writing ?? false}
        onRefresh={ki.retry}
        onAdd={ki.addIntegration}
        onToggle={(integration, enabled) => void ki.changeIntegration(integration.id, { enabled })}
        onTest={(integration) => void ki.checkIntegration(integration.id)}
        onSync={(integration) => void ki.runSync(integration.id)}
        onAuthorize={(integration) => void ki.authorizeIntegration(integration.id)}
        onDelete={(integration) => void ki.removeIntegration(integration.id)}
      />
    </div>
  );
}
