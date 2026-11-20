"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, Download, LoaderCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { apiRequest } from "@/lib/api";
import {
  describeModelStatus,
  formatModelBytes,
  missingItems,
  modelProgress,
  parseRecommendation,
  type ModelRecommendation,
  type RecommendedItem,
} from "@/lib/models";
import { t, translateServerText } from "@/lib/i18n";

/** Waehrend ein Download laeuft, alle zwei Sekunden nachsehen. */
const NACHSEHEN_MS = 2000;

function downloadPath(item: RecommendedItem) {
  const id = encodeURIComponent(item.id);
  return item.kind === "voice" ? `/v1/speech/voices/${id}/download` : `/v1/models/${id}/download`;
}

/**
 * Oben auf der KI-Seite: was zu diesem Rechner passt, mit einem Knopf für alles
 * und einem je Eintrag. Die Regeln stehen im Backend (`model_recommendation.py`),
 * damit die Seite nicht eine zweite Fassung davon hat.
 */
export function RecommendedModelsCard() {
  const [empfehlung, setEmpfehlung] = useState<ModelRecommendation | null>(null);
  const [fehler, setFehler] = useState<string | null>(null);

  const laden = useCallback(async () => {
    try {
      setEmpfehlung(parseRecommendation(await apiRequest("/v1/models/recommendation")));
      setFehler(null);
    } catch {
      setFehler(t("Die Empfehlung ließ sich nicht laden."));
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void laden(), 0);
    return () => window.clearTimeout(timer);
  }, [laden]);

  const laeuft = empfehlung?.items.some((item) => item.status.status === "downloading") ?? false;
  useEffect(() => {
    if (!laeuft) return;
    const timer = window.setInterval(() => void laden(), NACHSEHEN_MS);
    return () => window.clearInterval(timer);
  }, [laeuft, laden]);

  async function starten(items: RecommendedItem[]) {
    try {
      // Nacheinander: der Download-Manager sperrt je Modell, nicht global - parallel ginge auch,
      // aber nacheinander gestartet sieht man sofort, welcher Eintrag scheitert.
      for (const item of items) {
        await apiRequest(downloadPath(item), { method: "POST" });
      }
      setFehler(null);
    } catch (ursache) {
      setFehler(ursache instanceof Error ? translateServerText(ursache.message) : t("Der Download ließ sich nicht starten."));
    }
    await laden();
  }

  const fehlt = empfehlung ? missingItems(empfehlung.items) : [];
  const fehltGroesse = fehlt.reduce((summe, item) => summe + item.size_bytes, 0);
  const alleDa = empfehlung !== null && empfehlung.items.length > 0 && empfehlung.items.every((item) => item.status.status === "installed");

  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-3 space-y-0">
        <div className="space-y-1">
          <CardTitle>{t("Empfohlen für diesen Rechner")}</CardTitle>
          {/* Sichtbar, nicht `CardDescription` (die ist im Design nur für Screenreader). */}
          <p className="text-[13px] text-muted-foreground">
            {empfehlung?.hardware ? translateServerText(empfehlung.hardware) : t("Slearn prüft Arbeitsspeicher und Grafikkarte …")}
          </p>
        </div>
        {alleDa ? (
          <Badge variant="success">
            <Check className="mr-1 h-3 w-3" aria-hidden="true" />
            {t("Alles bereit")}
          </Badge>
        ) : fehlt.length > 0 ? (
          <Button type="button" onClick={() => void starten(fehlt)}>
            <Download aria-hidden="true" />
            {t("Alles herunterladen ({groesse})", { groesse: formatModelBytes(fehltGroesse) })}
          </Button>
        ) : null}
      </CardHeader>
      <CardContent className="space-y-3">
        {fehler ? (
          <p className="text-[15px] text-destructive" role="alert">
            {fehler}
          </p>
        ) : null}
        {empfehlung === null && !fehler ? <Skeleton className="h-32 w-full" /> : null}
        <ul className="divide-y divide-border">
          {empfehlung?.items.map((item) => {
            const stand = item.status.status;
            return (
              <li key={`${item.kind}-${item.id}`} className="flex flex-wrap items-center gap-3 py-2.5">
                <div className="min-w-0 flex-1">
                  <p className="text-[15px] font-medium">{translateServerText(item.purpose)}</p>
                  <p className="text-[13px] text-muted-foreground">
                    {item.label} · {formatModelBytes(item.size_bytes)}
                    {stand === "downloading" ? ` · ${modelProgress(item.status)} %` : ""}
                  </p>
                </div>
                {stand === "installed" ? (
                  <Badge variant="outline">{describeModelStatus(stand)}</Badge>
                ) : stand === "downloading" ? (
                  <Badge variant="outline">
                    <LoaderCircle className="mr-1 h-3 w-3 animate-spin" aria-hidden="true" />
                    {describeModelStatus(stand)}
                  </Badge>
                ) : (
                  <Button type="button" size="sm" variant="outline" onClick={() => void starten([item])}>
                    <Download aria-hidden="true" />
                    {t("Herunterladen")}
                  </Button>
                )}
              </li>
            );
          })}
        </ul>
        <p className="text-[13px] leading-5 text-muted-foreground">
          {t("Alles läuft auf diesem Rechner und kostet nichts. Einzelne Modelle, andere Größen und einen eigenen KI-Dienst findest du unter „Erweitert“.")}
        </p>
      </CardContent>
    </Card>
  );
}
