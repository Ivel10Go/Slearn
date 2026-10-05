"use client";

import { useCallback, useEffect, useState } from "react";
import { LoaderCircle, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
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

/*
 * Persoenliches Training: die Freigabe zum Sammeln, was gesammelt wurde und ob
 * dieser Rechner spaeter selbst trainieren kann.
 *
 * Das Training selbst gibt es noch nicht. Die Karte sagt das ausdruecklich -
 * eine Freigabe, deren Zweck man nicht sieht, waere keine informierte.
 */

function fehlertext(ursache: unknown, ersatz: string) {
  return ursache instanceof ApiError && ursache.message ? ursache.message : ersatz;
}

const ZAHLEN: { key: keyof TrainingStatus["stats"]; label: string }[] = [
  { key: "good", label: "Gut" },
  { key: "corrected", label: "Korrigiert" },
  { key: "bad", label: "Daneben" },
  { key: "unlabeled", label: "Ohne Signal" },
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
      setFehler(fehlertext(ursache, "Der Trainingsstand konnte nicht geladen werden."));
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
      setFehler(fehlertext(ursache, "Die Freigabe konnte nicht gespeichert werden."));
    } finally {
      setBeschaeftigt(false);
    }
  }

  async function einesLoeschen(id: number) {
    try {
      await deleteTrainingExample(id);
      await laden();
    } catch (ursache) {
      setFehler(fehlertext(ursache, "Das Beispiel konnte nicht gelöscht werden."));
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
      setFehler(fehlertext(ursache, "Die Beispiele konnten nicht gelöscht werden."));
    } finally {
      setBeschaeftigt(false);
    }
  }

  return (
    <Card>
      <CardHeader className="space-y-1">
        <CardTitle>Persönliches Training</CardTitle>
        <CardDescription>
          Mit deiner Freigabe hebt der Lernraum Antworten des Assistenten auf – zusammen mit dem, was du daraus gemacht hast: eingetragen, verworfen,
          Notiz gelöscht, Daumen. Daraus soll später ein Modell entstehen, das nur auf dich abgestimmt ist. Das Training selbst kommt in einem
          späteren Schritt; die Beispiele bleiben auf diesem Rechner.
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
                <span className="font-medium">Antworten fürs Training sammeln</span>
                <span className="block text-xs text-muted-foreground">
                  Standardmäßig aus. Ausschalten behält das Gesammelte – löschen kannst du es unten.
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
              <p className="font-medium text-foreground">{status.hardware.eligible ? "Dieser Rechner kann trainieren" : "Dieser Rechner trainiert vorerst nicht"}</p>
              <p className="text-muted-foreground">{status.hardware.reason}</p>
              {!status.hardware.eligible ? (
                <p className="mt-1 text-muted-foreground">Sammeln geht trotzdem – die Beispiele lassen sich später auf einem stärkeren Weg nutzen.</p>
              ) : null}
            </div>

            <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-md border border-border bg-border text-[13px] sm:grid-cols-5">
              <div className="bg-card px-3 py-2">
                <dt className="text-xs text-muted-foreground">Gesammelt</dt>
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
                  <p className="text-xs font-medium">Zuletzt gesammelt</p>
                  <Button type="button" size="sm" variant={loeschenFragen ? "default" : "ghost"} onClick={() => void alleLoeschen()} disabled={beschaeftigt}>
                    {beschaeftigt ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <Trash2 aria-hidden="true" />}
                    {loeschenFragen ? "Wirklich alle löschen?" : "Alle löschen"}
                  </Button>
                </div>
                <ul className="divide-y divide-border rounded-md border border-border">
                  {beispiele.map((beispiel) => (
                    <li key={beispiel.id} className="flex items-start justify-between gap-2 px-3 py-2">
                      <div className="min-w-0">
                        <p className="truncate text-[13px]">{beispiel.question || "(ohne Frage)"}</p>
                        <p className="line-clamp-1 text-xs text-muted-foreground">{beispiel.completion}</p>
                      </div>
                      <div className="flex shrink-0 items-center gap-1.5">
                        <Badge variant="outline">{beispiel.label ? TRAINING_LABELS[beispiel.label] : "Ohne Signal"}</Badge>
                        <button
                          type="button"
                          onClick={() => void einesLoeschen(beispiel.id)}
                          className="rounded-[5px] p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                          aria-label={`Beispiel „${beispiel.question}“ löschen`}
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
                {status.enabled ? "Noch nichts gesammelt. Stell dem Assistenten eine Frage." : "Noch nichts gesammelt."}
              </p>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}
