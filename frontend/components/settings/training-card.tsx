"use client";

import { useCallback, useEffect, useState } from "react";
import { LoaderCircle, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { TrainingRunPanel } from "@/components/settings/training-run-panel";
import { ApiError } from "@/lib/api";
import {
  TRAINING_LABELS,
  deleteAllTrainingExamples,
  deleteTrainingExample,
  getTrainingExamples,
  getTrainingStatus,
  setTrainingConsent,
  type TrainingExample,
  type TrainingStatus,
} from "@/lib/assistant";
import { t } from "@/lib/i18n";

/*
 * Persoenliches Training: die Freigabe zum Sammeln, was gesammelt wurde, ob
 * dieser Rechner trainieren kann - und, wenn ja, die Runden und Versionen
 * (``TrainingRunPanel``). Sammeln und Trainieren sind zwei Freigaben.
 */

function fehlertext(ursache: unknown, ersatz: string) {
  return ursache instanceof ApiError && ursache.message ? ursache.message : ersatz;
}

const ZAHLEN: { key: keyof TrainingStatus["stats"]; label: string }[] = [
  { key: "good", label: t("Gut") },
  { key: "corrected", label: t("Korrigiert") },
  { key: "bad", label: t("Daneben") },
  { key: "unlabeled", label: t("Ohne Signal") },
];

export function TrainingCard() {
  const [status, setStatus] = useState<TrainingStatus | null>(null);
  const [beispiele, setBeispiele] = useState<TrainingExample[]>([]);
  const [fehler, setFehler] = useState<string | null>(null);
  const [beschaeftigt, setBeschaeftigt] = useState(false);
  const [loeschenFragen, setLoeschenFragen] = useState(false);

  const laden = useCallback(async () => {
    try {
      const [neuerStatus, liste] = await Promise.all([getTrainingStatus(), getTrainingExamples()]);
      setStatus(neuerStatus);
      setBeispiele(liste);
      setFehler(null);
    } catch (ursache) {
      setFehler(fehlertext(ursache, t("Der Trainingsstand konnte nicht geladen werden.")));
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void laden(), 0);
    return () => window.clearTimeout(timer);
  }, [laden]);

  async function freigabe(enabled: boolean) {
    setBeschaeftigt(true);
    try {
      setStatus(await setTrainingConsent(enabled));
      setFehler(null);
    } catch (ursache) {
      setFehler(fehlertext(ursache, t("Die Freigabe konnte nicht gespeichert werden.")));
    } finally {
      setBeschaeftigt(false);
    }
  }

  async function einesLoeschen(id: number) {
    try {
      await deleteTrainingExample(id);
      await laden();
    } catch (ursache) {
      setFehler(fehlertext(ursache, t("Das Beispiel konnte nicht gelöscht werden.")));
    }
  }

  async function alleLoeschen() {
    if (!loeschenFragen) {
      setLoeschenFragen(true);
      return;
    }
    setBeschaeftigt(true);
    setLoeschenFragen(false);
    try {
      await deleteAllTrainingExamples();
      await laden();
    } catch (ursache) {
      setFehler(fehlertext(ursache, t("Die Beispiele konnten nicht gelöscht werden.")));
    } finally {
      setBeschaeftigt(false);
    }
  }

  return (
    <Card>
      <CardHeader className="space-y-1">
        <CardTitle>{t("Persönliches Training")}</CardTitle>
        <CardDescription>
          {t("Mit deiner Freigabe hebt der Lernraum Antworten des Assistenten auf – zusammen mit dem, was du daraus gemacht hast: eingetragen, verworfen, Notiz gelöscht, Daumen. Auf einem Rechner mit passender Grafikkarte entsteht daraus eine Ergänzung zum Modell, die nur auf dich abgestimmt ist. Beispiele und Training bleiben auf diesem Rechner.")}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {fehler ? (
          <p className="rounded-md border border-foreground/30 bg-muted px-3 py-2 text-[13px]" role="alert">
            {fehler}
          </p>
        ) : null}

        {status === null ? (
          fehler ? null : <Skeleton className="h-24 w-full" />
        ) : (
          <>
            <label className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-2.5 text-[13px]">
              <span>
                <span className="font-medium">{t("Antworten fürs Training sammeln")}</span>
                <span className="block text-xs text-muted-foreground">
                  {t("Standardmäßig aus. Ausschalten behält das Gesammelte – löschen kannst du es unten.")}
                </span>
              </span>
              <input
                type="checkbox"
                className="h-4 w-4 accent-foreground"
                checked={status.enabled}
                disabled={beschaeftigt}
                onChange={(event) => void freigabe(event.target.checked)}
              />
            </label>

            <div className="rounded-md border border-border px-3 py-2.5 text-xs leading-5">
              <p className="font-medium text-foreground">{status.hardware.eligible ? t("Dieser Rechner kann trainieren") : t("Dieser Rechner trainiert vorerst nicht")}</p>
              <p className="text-muted-foreground">{status.hardware.reason}</p>
              {!status.hardware.eligible ? (
                <p className="mt-1 text-muted-foreground">{t("Sammeln geht trotzdem – die Beispiele lassen sich später auf einem stärkeren Weg nutzen.")}</p>
              ) : null}
            </div>

            {status.hardware.eligible ? <TrainingRunPanel status={status} onStatus={setStatus} /> : null}

            <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-md border border-border bg-border text-[13px] sm:grid-cols-5">
              <div className="bg-card px-3 py-2">
                <dt className="text-xs text-muted-foreground">{t("Gesammelt")}</dt>
                <dd className="font-medium tabular-nums">
                  {status.stats.total} <span className="text-xs font-normal text-muted-foreground">/ {status.max_examples}</span>
                </dd>
              </div>
              {ZAHLEN.map((zahl) => (
                <div key={zahl.key} className="bg-card px-3 py-2">
                  <dt className="text-xs text-muted-foreground">{zahl.label}</dt>
                  <dd className="font-medium tabular-nums">{status.stats[zahl.key]}</dd>
                </div>
              ))}
            </dl>

            {beispiele.length > 0 ? (
              <div className="space-y-2">
                <div className="flex items-center justify-between gap-2">
                  <p className="text-xs font-medium">{t("Zuletzt gesammelt")}</p>
                  <Button type="button" size="sm" variant={loeschenFragen ? "default" : "ghost"} onClick={() => void alleLoeschen()} disabled={beschaeftigt}>
                    {beschaeftigt ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <Trash2 aria-hidden="true" />}
                    {loeschenFragen ? t("Wirklich alle löschen?") : t("Alle löschen")}
                  </Button>
                </div>
                <ul className="divide-y divide-border rounded-md border border-border">
                  {beispiele.map((beispiel) => (
                    <li key={beispiel.id} className="flex items-start justify-between gap-2 px-3 py-2">
                      <div className="min-w-0">
                        <p className="truncate text-[13px]">{beispiel.question || t("(ohne Frage)")}</p>
                        <p className="line-clamp-1 text-xs text-muted-foreground">{beispiel.completion}</p>
                      </div>
                      <div className="flex shrink-0 items-center gap-1.5">
                        <Badge variant="outline">{beispiel.label ? TRAINING_LABELS[beispiel.label] : t("Ohne Signal")}</Badge>
                        <button
                          type="button"
                          onClick={() => void einesLoeschen(beispiel.id)}
                          className="rounded-[5px] p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                          aria-label={t("Beispiel „{frage}“ löschen", { frage: beispiel.question })}
                        >
                          <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                        </button>
                      </div>
                    </li>
                  ))}
                </ul>
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">
                {status.enabled ? t("Noch nichts gesammelt. Stell dem Assistenten eine Frage.") : t("Noch nichts gesammelt.")}
              </p>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}
