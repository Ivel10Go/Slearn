"use client";

import { RefreshCw } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { ModelCatalogCard } from "@/components/settings/model-catalog-card";
import { IntegrationsCard } from "@/components/settings/settings-view";
import { VoiceCatalogCard } from "@/components/settings/voice-catalog-card";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { describeStages } from "@/lib/ai-runtime";
import { useIntegrations } from "@/lib/use-integrations";
import { useSession } from "@/lib/use-session";

/** Die Art einer KI-Verbindung im Integrationskatalog. */
const AI_KIND = "ai_provider";

const ABSCHNITTE = [
  { id: "ki-dienst", label: "KI-Dienst" },
  { id: "ki-modelle", label: "Lokale Modelle" },
  { id: "ki-stimmen", label: "Stimmen" },
] as const;

/**
 * Springt zu einem Abschnitt, ohne den Hash zu setzen.
 *
 * Die Shell liest den Hash fuer den Aktivzustand der Navigation; `#ki-modelle`
 * liesse "KI & Modelle" in der Seitenleiste erloeschen.
 */
function springen(id: string) {
  document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
}

/**
 * Alles, was mit KI zu tun hat, auf einer Seite: die Verbindung zu einem
 * KI-Server, die Modelle, die der Lernraum selbst laedt, und die Stimmen fuer
 * die Sprachausgabe.
 *
 * Vorher lag das in den Einstellungen - die Verbindung in der Integrationsliste,
 * Modelle und Stimmen als zwei endlose Karten in der schmalen rechten Spalte.
 * Die drei gehoeren zusammen, weil sie eine Frage beantworten: woher kommt eine
 * Antwort, und bleibt sie auf diesem Rechner?
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
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[22px] font-semibold leading-8 tracking-tight">KI &amp; Modelle</h1>
          <p className="mt-0.5 max-w-2xl text-[13px] leading-5 text-muted-foreground">
            Woher Antworten, Tagessatz und Vorschläge kommen: ein KI-Server, Modelle auf diesem Rechner und Stimmen zum Vorlesen.
          </p>
        </div>
        <nav aria-label="Abschnitte" className="flex flex-wrap gap-1">
          {ABSCHNITTE.map((abschnitt) => (
            <Button key={abschnitt.id} type="button" size="sm" variant="ghost" onClick={() => springen(abschnitt.id)}>
              {abschnitt.label}
            </Button>
          ))}
        </nav>
      </header>

      {ki.error ? (
        <div className="flex flex-col gap-3 rounded-md border border-destructive/20 bg-destructive/10 px-4 py-2.5 text-sm text-destructive sm:flex-row sm:items-center sm:justify-between" role="alert">
          <span>{ki.error}</span>
          <Button type="button" size="sm" variant="outline" onClick={ki.retry}>
            <RefreshCw aria-hidden="true" />
            Erneut versuchen
          </Button>
        </div>
      ) : null}

      {ki.actionError ? (
        <p className="rounded-md border border-destructive/20 bg-destructive/10 px-4 py-2.5 text-sm font-medium text-destructive" role="alert">
          {ki.actionError}
        </p>
      ) : null}

      <div id="ki-dienst" className="grid scroll-mt-20 items-start gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,340px)]">
        {laedt ? (
          <Skeleton className="h-48 w-full rounded-md" />
        ) : (
          <IntegrationsCard
            title="KI-Dienst"
            description="Ein Server wie Ollama oder LM Studio auf deinem Rechner, oder ein Online-Dienst mit OpenAI-kompatibler Schnittstelle."
            emptyTitle="Kein KI-Dienst verbunden"
            emptyDescription="Ohne Dienst antwortet der Lernraum aus deinen Daten und Dokumenten. Unter „Hinzufügen“ stehen Vorschläge für bekannte lokale Server."
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

        {/*
          Die Reihenfolge der Wege steht neben der Verbindung und nicht in jeder
          Zeile: sie gilt fuer alle zusammen. Der Text kommt vom Backend
          (`GET /v1/ai-runtime`), damit Oberflaeche und Routing nicht zwei
          Fassungen derselben Regel haben.
        */}
        <Card>
          <CardHeader className="space-y-1">
            <CardTitle>So entsteht eine Antwort</CardTitle>
            <CardDescription>Der erste Weg, der antwortet, gewinnt. Jede Antwort zeigt an, woher sie kommt.</CardDescription>
          </CardHeader>
          <CardContent>
            {ki.runtime ? (
              <ol className="space-y-1.5 border-l-2 border-border pl-3 text-xs leading-5 text-muted-foreground">
                {describeStages(ki.runtime).map((stufe) => (
                  <li key={stufe}>{stufe}</li>
                ))}
              </ol>
            ) : laedt ? (
              <Skeleton className="h-16 w-full" />
            ) : (
              <p className="text-xs leading-5 text-muted-foreground">
                Lokaler Server, dann Online-Dienst, sonst eine Antwort aus deinen Daten. Die genaue Reihenfolge konnte nicht geladen werden.
              </p>
            )}
          </CardContent>
        </Card>
      </div>

      <div id="ki-modelle" className="scroll-mt-20">
        <ModelCatalogCard />
      </div>

      {/*
        Die Stimmen stehen unter den Modellen und nicht darin: eine Stimme ist
        eine Stimme *in einer* Sprache, ein Whisper-Modell kennt viele.
        Zusammengelegt waere genau die Entscheidung unsichtbar, die man hier
        treffen muss.
      */}
      <div id="ki-stimmen" className="scroll-mt-20">
        <VoiceCatalogCard />
      </div>
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
