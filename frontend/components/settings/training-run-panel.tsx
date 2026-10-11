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
  setMaterialsTraining,
  startTrainingRun,
  type TrainingAdapters,
  type TrainingRun,
  type TrainingStatus,
} from "@/lib/assistant";
import { intlLocale, t } from "@/lib/i18n";

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
  return new Date(iso).toLocaleString(intlLocale(), { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
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
      setFehler(fehlertext(ursache, t("Der Trainingsstand konnte nicht geladen werden.")));
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

  const kannTrainieren = status.hardware.eligible && (status.enabled || status.materials);
  const offen = lauf !== null && lauf.status !== "done";
  const anteil = lauf && lauf.total_steps > 0 ? Math.min(100, Math.round((lauf.step / lauf.total_steps) * 100)) : 0;

  return (
    <div className="space-y-3 rounded-md border border-border px-3 py-2.5">
      <p className="text-[13px] font-medium">{t("Training auf diesem Rechner")}</p>

      {fehler ? (
        <p className="rounded-md border border-foreground/30 bg-muted px-3 py-2 text-[13px]" role="alert">
          {fehler}
        </p>
      ) : null}

      <label className="flex items-center justify-between gap-3 text-[13px]">
        <span>
          <span className="font-medium">{t("Automatisch trainieren")}</span>
          <span className="block text-xs text-muted-foreground">
            {t("Nur am Netzteil und wenn du den Rechner zwei Minuten nicht benutzt. Arbeitest du weiter, hält es nach dem laufenden Schritt an und macht beim nächsten Mal dort weiter. Eine neue Runde beginnt nach 20 neuen bewerteten Antworten.")}
          </span>
        </span>
        <input
          type="checkbox"
          className="h-4 w-4 accent-foreground"
          checked={status.auto}
          disabled={beschaeftigt || !kannTrainieren}
          onChange={(event) => void ausfuehren(async () => onStatus(await setAutoTraining(event.target.checked)), t("Die Einstellung konnte nicht gespeichert werden."))}
        />
      </label>
      <label className="flex items-center justify-between gap-3 text-[13px]">
        <span>
          <span className="font-medium">{t("Aufschriebe und Arbeitsblätter nutzen")}</span>
          <span className="block text-xs text-muted-foreground">
            {t("Das Modell lernt den Aufbau deines Hefts – Überschriften, Stichpunkte, Merke-Kästen, Abkürzungen – und das Antworten mit deinem Material. Der Stoff selbst bleibt in den Dateien: löschst du eine, ist sie weg. Alles bleibt auf diesem Rechner.")}
          </span>
        </span>
        <input
          type="checkbox"
          className="h-4 w-4 accent-foreground"
          checked={status.materials}
          disabled={beschaeftigt || !status.hardware.eligible}
          onChange={(event) =>
            void ausfuehren(async () => onStatus(await setMaterialsTraining(event.target.checked)), t("Die Einstellung konnte nicht gespeichert werden."))
          }
        />
      </label>
      {!status.enabled && !status.materials ? (
        <p className="text-xs text-muted-foreground">{t("Schalte das Sammeln oder dein Material frei, dann gibt es etwas zu trainieren.")}</p>
      ) : null}

      {lauf ? (
        <div className="space-y-2 border-t border-border pt-3">
          <div className="flex items-center justify-between gap-2 text-[13px]">
            <span className="flex items-center gap-2">
              {aktiv ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : null}
              {TRAINING_RUN_STATUS[lauf.status]}
            </span>
            <span className="text-xs tabular-nums text-muted-foreground">
              {lauf.total_steps > 0 ? t("Schritt {n} von {gesamt}", { n: lauf.step, gesamt: lauf.total_steps }) : null}
              {lauf.seconds_per_step && aktiv ? ` · ${t("{n} s je Schritt", { n: lauf.seconds_per_step.toLocaleString(intlLocale()) })}` : null}
            </span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-muted" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={anteil}>
            <div className="h-full bg-foreground transition-[width]" style={{ width: `${anteil}%` }} />
          </div>
          {lauf.qa_total > 0 ? (
            <p className="text-xs">
              {t("Fragen zu deinem Material: {geuebt} von {gesamt} geübt, {behalten} mit Beleg aus dem Material behalten", { geuebt: lauf.qa_done, gesamt: lauf.qa_total, behalten: lauf.qa_kept })}
            </p>
          ) : null}
          <p className="text-xs text-muted-foreground">
            {t("{n} Beispiele, {modell}", { n: lauf.example_count, modell: lauf.base_model_id })}
            {lauf.skipped_too_long > 0 ? ` · ${t("{n} zu lang, übersprungen", { n: lauf.skipped_too_long })}` : ""}
          </p>
          {lauf.eval_score_base !== null || lauf.eval_score !== null ? (
            <p className="text-xs">
              {t("Art und Datum richtig: {ohne} ohne, {mit} mit persönlichem Training", { ohne: prozent(lauf.eval_score_base), mit: prozent(lauf.eval_score) })}
            </p>
          ) : null}
          {lauf.status === "done" && lauf.adapter_version !== null ? (
            <p className="text-xs text-muted-foreground">
              {lauf.activated
                ? t("Version {n} ist aktiv – der Assistent nutzt sie ab der nächsten Antwort.", { n: lauf.adapter_version })
                : t("Version {n} war auf den Prüfbeispielen schlechter und bleibt aus.", { n: lauf.adapter_version })}
            </p>
          ) : null}
          {lauf.error ? <p className="text-xs">{lauf.error}</p> : null}
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">{t("Noch keine Trainingsrunde.")}</p>
      )}

      <div className="flex flex-wrap gap-2">
        {aktiv ? (
          <Button type="button" size="sm" variant="outline" disabled={beschaeftigt} onClick={() => void ausfuehren(pauseTrainingRun, t("Das Training ließ sich nicht anhalten."))}>
            <Pause aria-hidden="true" />
            {t("Pausieren")}
          </Button>
        ) : (
          <Button type="button" size="sm" disabled={beschaeftigt || !kannTrainieren} onClick={() => void ausfuehren(startTrainingRun, t("Das Training ließ sich nicht starten."))}>
            {beschaeftigt ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <Play aria-hidden="true" />}
            {offen ? t("Fortsetzen") : t("Jetzt trainieren")}
          </Button>
        )}
        {offen && !aktiv ? (
          <Button type="button" size="sm" variant="ghost" disabled={beschaeftigt} onClick={() => void ausfuehren(discardTrainingRun, t("Die Runde ließ sich nicht verwerfen."))}>
            <Trash2 aria-hidden="true" />
            {t("Runde verwerfen")}
          </Button>
        ) : null}
      </div>

      {adapter && adapter.versions.length > 0 ? (
        <div className="space-y-2 border-t border-border pt-3">
          <div className="flex items-center justify-between gap-2">
            <p className="text-xs font-medium">{t("Versionen")}</p>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              disabled={beschaeftigt || adapter.active_version === null}
              onClick={() => void ausfuehren(rollbackTrainingAdapter, t("Die Version ließ sich nicht zurücksetzen."))}
            >
              <RotateCcw aria-hidden="true" />
              {t("Eine Version zurück")}
            </Button>
          </div>
          <ul className="divide-y divide-border rounded-md border border-border">
            {adapter.versions.map((version) => (
              <li key={version.version} className="flex items-center justify-between gap-2 px-3 py-2 text-[13px]">
                <span>
                  {t("Version {n}", { n: version.version })}
                  <span className="block text-xs text-muted-foreground">
                    {datum(version.created_at)} · {t("{n} Beispiele · Art und Datum {wert}", { n: version.example_count, wert: prozent(version.eval_score) })}
                  </span>
                </span>
                {version.active ? <Badge variant="outline">{t("Aktiv")}</Badge> : null}
              </li>
            ))}
          </ul>
          {adapter.active_version === null ? <p className="text-xs text-muted-foreground">{t("Gerade läuft das Modell ohne persönliches Training.")}</p> : null}
        </div>
      ) : null}
    </div>
  );
}
