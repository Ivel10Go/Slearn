"use client";

import { useCallback, useEffect, useState } from "react";
import { LoaderCircle, Pause, Play, RotateCcw, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api";
import {
  TRAINING_RUN_STATUS,
  discardTrainingRun,
  getTrainingAdapters,
  getTrainingRun,
  pauseTrainingRun,
  prozent,
  rollbackTrainingAdapter,
  setAutoTraining,
  startTrainingRun,
  type TrainingAdapters,
  type TrainingRun,
  type TrainingStatus,
} from "@/lib/assistant";

/*
 * Training auf diesem Rechner: die laufende Runde, die Automatik und die
 * Versionen des persoenlichen Adapters.
 *
 * Solange der Trainer laeuft, wird alle 4 s nachgesehen - der Stand steht in
 * einer Datei, die der Trainer nach jedem Schritt schreibt, eine Abfrage kostet
 * also nichts.
 */

const ABFRAGE_MS = 4_000;

function fehlertext(ursache: unknown, ersatz: string) {
  return ursache instanceof ApiError && ursache.message ? ursache.message : ersatz;
}

function datum(iso: string | null) {
  if (!iso) {
    return "";
  }
  return new Date(iso).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export function TrainingRunPanel({ status, onStatus }: { status: TrainingStatus; onStatus: (status: TrainingStatus) => void }) {
  const [lauf, setLauf] = useState<TrainingRun | null>(null);
  const [adapter, setAdapter] = useState<TrainingAdapters | null>(null);
  const [fehler, setFehler] = useState<string | null>(null);
  const [beschaeftigt, setBeschaeftigt] = useState(false);

  const laden = useCallback(async () => {
    try {
      setLauf(await getTrainingRun());
      // Ohne geladenes Textmodell gibt es keine Versionen - das ist kein Fehler.
      setAdapter(await getTrainingAdapters().catch(() => null));
    } catch (ursache) {
      setFehler(fehlertext(ursache, "Der Trainingsstand konnte nicht geladen werden."));
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void laden(), 0);
    return () => window.clearTimeout(timer);
  }, [laden]);

  const aktiv = lauf?.active ?? false;
  useEffect(() => {
    if (!aktiv) {
      return;
    }
    const timer = window.setInterval(() => void laden(), ABFRAGE_MS);
    return () => window.clearInterval(timer);
  }, [aktiv, laden]);

  async function ausfuehren(aktion: () => Promise<unknown>, ersatz: string) {
    setBeschaeftigt(true);
    try {
      await aktion();
      setFehler(null);
      await laden();
    } catch (ursache) {
      setFehler(fehlertext(ursache, ersatz));
    } finally {
      setBeschaeftigt(false);
    }
  }

  const kannTrainieren = status.hardware.eligible && status.enabled;
  const offen = lauf !== null && lauf.status !== "done";
  const anteil = lauf && lauf.total_steps > 0 ? Math.min(100, Math.round((lauf.step / lauf.total_steps) * 100)) : 0;

  return (
    <div className="space-y-3 rounded-md border border-border px-3 py-2.5">
      <p className="text-[13px] font-medium">Training auf diesem Rechner</p>

      {fehler ? (
        <p className="rounded-md border border-foreground/30 bg-muted px-3 py-2 text-[13px]" role="alert">
          {fehler}
        </p>
      ) : null}

      <label className="flex items-center justify-between gap-3 text-[13px]">
        <span>
          <span className="font-medium">Automatisch trainieren</span>
          <span className="block text-xs text-muted-foreground">
            Nur am Netzteil und wenn du den Rechner zwei Minuten nicht benutzt. Arbeitest du weiter, hält es nach dem laufenden Schritt an und
            macht beim nächsten Mal dort weiter. Eine neue Runde beginnt nach 20 neuen bewerteten Antworten.
          </span>
        </span>
        <input
          type="checkbox"
          className="h-4 w-4 accent-foreground"
          checked={status.auto}
          disabled={beschaeftigt || !kannTrainieren}
          onChange={(event) => void ausfuehren(async () => onStatus(await setAutoTraining(event.target.checked)), "Die Einstellung konnte nicht gespeichert werden.")}
        />
      </label>
      {!status.enabled ? <p className="text-xs text-muted-foreground">Erst mit eingeschaltetem Sammeln gibt es etwas zu trainieren.</p> : null}

      {lauf ? (
        <div className="space-y-2 border-t border-border pt-3">
          <div className="flex items-center justify-between gap-2 text-[13px]">
            <span className="flex items-center gap-2">
              {aktiv ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : null}
              {TRAINING_RUN_STATUS[lauf.status]}
            </span>
            <span className="text-xs tabular-nums text-muted-foreground">
              {lauf.total_steps > 0 ? `Schritt ${lauf.step} von ${lauf.total_steps}` : null}
              {lauf.seconds_per_step && aktiv ? ` · ${lauf.seconds_per_step.toLocaleString("de-DE")} s je Schritt` : null}
            </span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-muted" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={anteil}>
            <div className="h-full bg-foreground transition-[width]" style={{ width: `${anteil}%` }} />
          </div>
          <p className="text-xs text-muted-foreground">
            {lauf.example_count} Beispiele, {lauf.base_model_id}
            {lauf.skipped_too_long > 0 ? ` · ${lauf.skipped_too_long} zu lang, übersprungen` : ""}
          </p>
          {lauf.eval_score_base !== null || lauf.eval_score !== null ? (
            <p className="text-xs">
              Art und Datum richtig: {prozent(lauf.eval_score_base)} ohne, {prozent(lauf.eval_score)} mit persönlichem Training
            </p>
          ) : null}
          {lauf.status === "done" && lauf.adapter_version !== null ? (
            <p className="text-xs text-muted-foreground">
              {lauf.activated
                ? `Version ${lauf.adapter_version} ist aktiv – der Assistent nutzt sie ab der nächsten Antwort.`
                : `Version ${lauf.adapter_version} war auf den Prüfbeispielen schlechter und bleibt aus.`}
            </p>
          ) : null}
          {lauf.error ? <p className="text-xs">{lauf.error}</p> : null}
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">Noch keine Trainingsrunde.</p>
      )}

      <div className="flex flex-wrap gap-2">
        {aktiv ? (
          <Button type="button" size="sm" variant="outline" disabled={beschaeftigt} onClick={() => void ausfuehren(pauseTrainingRun, "Das Training ließ sich nicht anhalten.")}>
            <Pause aria-hidden="true" />
            Pausieren
          </Button>
        ) : (
          <Button type="button" size="sm" disabled={beschaeftigt || !kannTrainieren} onClick={() => void ausfuehren(startTrainingRun, "Das Training ließ sich nicht starten.")}>
            {beschaeftigt ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <Play aria-hidden="true" />}
            {offen ? "Fortsetzen" : "Jetzt trainieren"}
          </Button>
        )}
        {offen && !aktiv ? (
          <Button type="button" size="sm" variant="ghost" disabled={beschaeftigt} onClick={() => void ausfuehren(discardTrainingRun, "Die Runde ließ sich nicht verwerfen.")}>
            <Trash2 aria-hidden="true" />
            Runde verwerfen
          </Button>
        ) : null}
      </div>

      {adapter && adapter.versions.length > 0 ? (
        <div className="space-y-2 border-t border-border pt-3">
          <div className="flex items-center justify-between gap-2">
            <p className="text-xs font-medium">Versionen</p>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              disabled={beschaeftigt || adapter.active_version === null}
              onClick={() => void ausfuehren(rollbackTrainingAdapter, "Die Version ließ sich nicht zurücksetzen.")}
            >
              <RotateCcw aria-hidden="true" />
              Eine Version zurück
            </Button>
          </div>
          <ul className="divide-y divide-border rounded-md border border-border">
            {adapter.versions.map((version) => (
              <li key={version.version} className="flex items-center justify-between gap-2 px-3 py-2 text-[13px]">
                <span>
                  Version {version.version}
                  <span className="block text-xs text-muted-foreground">
                    {datum(version.created_at)} · {version.example_count} Beispiele · Art und Datum {prozent(version.eval_score)}
                  </span>
                </span>
                {version.active ? <Badge variant="outline">Aktiv</Badge> : null}
              </li>
            ))}
          </ul>
          {adapter.active_version === null ? <p className="text-xs text-muted-foreground">Gerade läuft das Modell ohne persönliches Training.</p> : null}
        </div>
      ) : null}
    </div>
  );
}
