"use client";

import type { ReactNode } from "react";
import { RefreshCw } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { ModelCatalogCard } from "@/components/settings/model-catalog-card";
import { RecommendedModelsCard } from "@/components/settings/recommended-models-card";
import { IntegrationsCard } from "@/components/settings/settings-view";
import { TrainingCard } from "@/components/settings/training-card";
import { VoiceCatalogCard } from "@/components/settings/voice-catalog-card";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { describeStages } from "@/lib/ai-runtime";
import { useIntegrations } from "@/lib/use-integrations";
import { useSession } from "@/lib/use-session";
import { t } from "@/lib/i18n";

/** Die Art einer KI-Verbindung im Integrationskatalog. */
const AI_KIND = "ai_provider";

/** Ein zugeklappter Abschnitt. Nativ, damit Tastatur und Screenreader ohne eigenen Code stimmen. */
function Aufklapper({ titel, children }: { titel: string; children: ReactNode }) {
  return (
    <details className="group">
      <summary className="cursor-pointer select-none rounded-full px-3 py-1.5 text-[15px] text-muted-foreground hover:text-foreground">
        {titel}
      </summary>
      <div className="mt-3 space-y-4">{children}</div>
    </details>
  );
}

/**
 * Alles, was mit KI zu tun hat - aber zuerst nur das, was man braucht.
 *
 * Oben steht die Empfehlung fuer diesen Rechner mit einem Knopf. Vorher standen
 * hier KI-Dienst, Modellkatalog, Stimmen und Training untereinander, und wer nur
 * "die KI soll gehen" wollte, musste zwischen sieben Modellen waehlen.
 * Alles Einzelne bleibt erreichbar: "Erweitert" fuer Modelle, Stimmen und einen
 * eigenen KI-Dienst, "Für Entwickler" fuer Antwortwege und Training.
 *
 * Die Verbindungsliste nutzt dieselbe Karte wie die Einstellungen, nur auf die
 * KI-Art beschraenkt - eine zweite Fassung desselben Formulars waere eine
 * zweite Stelle, an der die `host.docker.internal`-Falle erklaert werden muss.
 */
function KiBereich() {
  const ki = useIntegrations();
  const verbindungen = ki.integrations.filter((integration) => integration.kind === AI_KIND);
  const katalog = ki.catalog.filter((entry) => entry.kind === AI_KIND);
  const schreibenErlaubt = ki.writing?.share_calendar_for_writing ?? false;
  const laedt = ki.status === "loading" && ki.catalog.length === 0;

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-[26px] font-semibold leading-9 tracking-tight">{t("KI & Modelle")}</h1>
        <p className="sr-only">
          {t("Woher Antworten, Tagessatz und Vorschläge kommen: ein KI-Server, Modelle auf diesem Rechner und Stimmen zum Vorlesen.")}
        </p>
      </header>

      <RecommendedModelsCard />

      {ki.error ? (
        <div className="flex flex-col gap-3 rounded-md border border-destructive/20 bg-destructive/10 px-4 py-2.5 text-[15px] text-destructive sm:flex-row sm:items-center sm:justify-between" role="alert">
          <span>{ki.error}</span>
          <Button type="button" size="sm" variant="outline" onClick={ki.retry}>
            <RefreshCw aria-hidden="true" />
            {t("Erneut versuchen")}
          </Button>
        </div>
      ) : null}

      {ki.actionError ? (
        <p className="rounded-md border border-destructive/20 bg-destructive/10 px-4 py-2.5 text-[15px] font-medium text-destructive" role="alert">
          {ki.actionError}
        </p>
      ) : null}

      <Aufklapper titel={t("Erweitert: einzelne Modelle, Stimmen und eigener KI-Dienst")}>
        <ModelCatalogCard />
        {/*
          Die Stimmen stehen unter den Modellen und nicht darin: eine Stimme ist
          eine Stimme *in einer* Sprache, ein Whisper-Modell kennt viele.
        */}
        <VoiceCatalogCard />
        {laedt ? (
          <Skeleton className="h-48 w-full rounded-md" />
        ) : (
          <IntegrationsCard
            title={t("KI-Dienst")}
            description={t("Ein Server wie Ollama oder LM Studio auf deinem Rechner, oder ein Online-Dienst mit OpenAI-kompatibler Schnittstelle.")}
            emptyTitle={t("Kein KI-Dienst verbunden")}
            emptyDescription={t("Ohne Dienst antwortet Slearn aus deinen Daten und Dokumenten. Unter „Hinzufügen“ stehen Vorschläge für bekannte lokale Server.")}
            integrations={verbindungen}
            catalog={katalog}
            runtime={ki.runtime}
            pendingAction={ki.pendingAction}
            testResults={ki.testResults}
            syncResults={ki.syncResults}
            isRefreshing={ki.status === "loading"}
            canWriteRemotely={schreibenErlaubt}
            onRefresh={ki.retry}
            onAdd={ki.addIntegration}
            onToggle={(integration, enabled) => void ki.changeIntegration(integration.id, { enabled })}
            onTest={(integration) => void ki.checkIntegration(integration.id)}
            onSync={(integration) => void ki.runSync(integration.id)}
            onAuthorize={(integration) => void ki.authorizeIntegration(integration.id)}
            onDelete={(integration) => void ki.removeIntegration(integration.id)}
          />
        )}
      </Aufklapper>

      <Aufklapper titel={t("Für Entwickler: Antwortwege und persönliches Training")}>
        {/*
          Die Reihenfolge der Wege steht neben der Verbindung und nicht in jeder
          Zeile: sie gilt fuer alle zusammen. Der Text kommt vom Backend
          (`GET /v1/ai-runtime`), damit Oberflaeche und Routing nicht zwei
          Fassungen derselben Regel haben.
        */}
        <Card>
          <CardHeader className="space-y-1">
            <CardTitle>{t("So entsteht eine Antwort")}</CardTitle>
            <CardDescription>{t("Der erste Weg, der antwortet, gewinnt. Jede Antwort zeigt an, woher sie kommt.")}</CardDescription>
          </CardHeader>
          <CardContent>
            {ki.runtime ? (
              <ol className="space-y-1.5 border-l-2 border-border pl-3 text-[13px] leading-5 text-muted-foreground">
                {describeStages(ki.runtime).map((stufe) => (
                  <li key={stufe}>{stufe}</li>
                ))}
              </ol>
            ) : laedt ? (
              <Skeleton className="h-16 w-full" />
            ) : (
              <p className="text-[13px] leading-5 text-muted-foreground">
                {t("Lokaler Server, dann Online-Dienst, sonst eine Antwort aus deinen Daten. Die genaue Reihenfolge konnte nicht geladen werden.")}
              </p>
            )}
          </CardContent>
        </Card>
        <TrainingCard />
      </Aufklapper>
    </div>
  );
}

export default function KiPage() {
  const user = useSession();
  return (
    <AppShell user={user}>
      <KiBereich />
    </AppShell>
  );
}
