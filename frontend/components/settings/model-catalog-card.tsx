"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, Download, LoaderCircle, RefreshCw, Trash2, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { apiRequest } from "@/lib/api";
import {
  canCancelModelDownload,
  canDeleteModel,
  canStartModelDownload,
  describeModelStatus,
  describeModelTask,
  formatModelBytes,
  modelFitsFreeSpace,
  modelProgress,
  parseModelCatalog,
  type ModelCatalogPayload,
  type ModelPayload,
} from "@/lib/models";
import { cn } from "@/lib/utils";

/*
 * Der Modellkatalog und der Download-Manager, aus `GET /api/v1/models`
 * gelesen. Drei Entscheidungen, die man hier nicht wegdiskutieren kann:
 *
 * - **Der Download läuft im Backend, nicht im Browser.** Diese Karte schickt
 *   einen Knopfdruck und pollt danach den Stand. Ein Browser-Download könnte
 *   weder fortgesetzt, wenn der Tab zu ist, noch die SHA256 prüfen - und der
 *   Server weiß die Ablage ohnehin besser.
 * - **Der Fortschrittsbalken ist schwarz auf grau.** Das Layout ist monochrome
 *   und vollständig eckig; es gibt keine Farbklassen, keine Verläufe und keine
 *   Schatten. Der Zustand steht als deutsches Wort daneben, nicht als Ampelfarbe.
 * - **Größe und RAM-Bedarf werden beide gezeigt und beide als das bezeichnet,
 *   was sie sind.** Die Größe kommt aus den Metadaten des Herstellers, der
 *   RAM-Bedarf ist eine daraus abgeleitete Schätzung - der Server liefert den
 *   Satz dazu mit, und er steht hier im Kleingedruckten.
 */

const POLL_INTERVAL_MS = 2000;

function describeError(cause: unknown) {
  if (cause instanceof Error && cause.message) {
    return cause.message;
  }
  return "Der Modellkatalog konnte nicht geladen werden.";
}

/**
 * Der Balken: eine Haarlinie als Spur, eine gefüllte Fläche als Stand.
 *
 * Bewusst ohne Rundung (`rounded-*` ist global abgeschaltet) und ohne Farbe.
 * `role="progressbar"` mit den ARIA-Werten, weil die Zahl daneben allein nicht
 * vorgelesen wird.
 */
function ProgressBar({ model }: { model: ModelPayload }) {
  const anteil = modelProgress(model.status);
  const laeuft = model.status.status === "downloading";
  const beschriftung = `${anteil.toFixed(1).replace(".", ",")} Prozent geladen`;
  return (
    <div className="space-y-1">
      <div
        className="h-2 w-full border border-border bg-muted"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(anteil)}
        aria-valuetext={beschriftung}
        aria-label={`Ladefortschritt von ${model.label}`}
      >
        <div className={cn("h-full bg-foreground", laeuft ? "opacity-70" : "opacity-100")} style={{ width: `${anteil}%` }} />
      </div>
      <p className="text-[11px] font-semibold text-muted-foreground">
        <span className="tabular-nums">{anteil.toFixed(1).replace(".", ",")} %</span>
        {model.status.bytes_total > 0
          ? ` von ${formatModelBytes(model.status.bytes_total)}`
          : " von unbekannter Gesamtgröße"}
        {model.status.current_file ? ` · gerade ${model.status.current_file}` : ""}
      </p>
    </div>
  );
}

function ModelRow({
  model,
  storageFreeBytes,
  pendingAction,
  onStart,
  onCancel,
  onDelete,
}: {
  model: ModelPayload;
  storageFreeBytes: number | null;
  pendingAction: string | null;
  onStart: (model: ModelPayload) => void;
  onCancel: (model: ModelPayload) => void;
  onDelete: (model: ModelPayload) => void;
}) {
  const [bestaetigen, setBestaetigen] = useState(false);
  const laueft = model.status.status === "downloading";
  const istAmLaufen = pendingAction === `download:${model.id}`;
  const passt = modelFitsFreeSpace(model.size_bytes, storageFreeBytes);
  const zeigeFortschritt = model.status.status !== "not_downloaded";

  return (
    <li className="flex flex-col rounded-md border border-border/70 p-3.5">
      <div className="flex items-start justify-between gap-3">
        <p className="min-w-0 text-[13px] font-semibold leading-5">{model.label}</p>
        <Badge variant={model.status.status === "installed" ? "success" : "secondary"} className="shrink-0">
          {describeModelStatus(model.status.status)}
        </Badge>
      </div>

      {/*
        Die Aufgabe steht als Gruppenueberschrift, nicht in jeder Kachel.
        Groesse und RAM stehen in einer Zeile statt in zwei Kaesten: sie sind
        die beiden Zahlen, nach denen man waehlt, und nebeneinander vergleicht
        man sie ueber die Kacheln hinweg. Die Herleitung der RAM-Schaetzung
        steht im `title` - sie ist Beleg, nicht Entscheidung.
      */}
      <p className="mt-1 flex flex-wrap gap-x-2 text-xs text-muted-foreground">
        <span className="tabular-nums">{formatModelBytes(model.size_bytes)}</span>
        <span aria-hidden="true">·</span>
        <span className="tabular-nums" title={model.ram_basis ? `Gerechnet aus: ${model.ram_basis}.` : undefined}>
          RAM {model.ram_estimate_bytes > 0 ? `ab ${model.ram_estimate_gib.toFixed(1).replace(".", ",")} GiB` : "unbekannt"} (Schätzung)
        </span>
        {model.status.verified ? (
          <>
            <span aria-hidden="true">·</span>
            <span>Prüfsumme bestätigt</span>
          </>
        ) : null}
      </p>

      <p className="mt-2 text-xs leading-5 text-muted-foreground">{model.description}</p>

      {passt === false ? (
        <p className="mt-2 flex items-start gap-2 text-xs font-semibold">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          Der freie Speicher ist kleiner als dieses Modell. Der Download bricht vorher ab und lässt eine Reserve.
        </p>
      ) : null}

      {zeigeFortschritt ? (
        <div className="mt-2.5">
          <ProgressBar model={model} />
        </div>
      ) : null}

      {/*
        Eine Live-Region muss vor dem Text im DOM stehen, sonst wird er nicht
        angesagt. Deshalb ist der Container immer da und nur sein Inhalt wechselt.
        "Noch nicht geladen" sagt schon das Badge - der Satz steht nur, wenn er
        mehr sagt.
      */}
      <div className={cn(zeigeFortschritt && model.status.message && "mt-2")} role="status" aria-live="polite">
        {zeigeFortschritt && model.status.message ? <p className="text-xs leading-5 text-muted-foreground">{model.status.message}</p> : null}
      </div>

      <div className="mt-auto flex flex-wrap items-center gap-2 pt-3">
        {!laueft ? (
          <Button type="button" size="sm" onClick={() => onStart(model)} disabled={!canStartModelDownload(model.status) || istAmLaufen}>
            {istAmLaufen ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <Download aria-hidden="true" />}
            {model.status.status === "not_downloaded" ? "Herunterladen" : "Fortsetzen"}
          </Button>
        ) : (
          <Button type="button" size="sm" variant="outline" onClick={() => onCancel(model)} disabled={!canCancelModelDownload(model.status)}>
            <X aria-hidden="true" />
            Abbrechen
          </Button>
        )}

        {bestaetigen ? (
          <>
            <Button type="button" size="sm" variant="destructive" onClick={() => onDelete(model)} disabled={laueft}>
              <Trash2 aria-hidden="true" />
              Wirklich entfernen
            </Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => setBestaetigen(false)} disabled={laueft}>
              Abbrechen
            </Button>
          </>
        ) : (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={() => setBestaetigen(true)}
            disabled={!canDeleteModel(model.status)}
            aria-expanded={bestaetigen}
          >
            <Trash2 aria-hidden="true" />
            Entfernen
          </Button>
        )}
      </div>
    </li>
  );
}

export function ModelCatalogCard() {
  const anfrageId = useRef(0);
  const [katalog, setKatalog] = useState<ModelCatalogPayload | null>(null);
  const [fehler, setFehler] = useState<string | null>(null);
  const [laedt, setLaedt] = useState(true);
  const [pendingAction, setPendingAction] = useState<string | null>(null);

  const laden = useCallback(async () => {
    const aktuelleAnfrage = ++anfrageId.current;
    try {
      const antwort = await apiRequest("/v1/models");
      if (aktuelleAnfrage !== anfrageId.current) {
        return;
      }
      const geparst = parseModelCatalog(antwort);
      setKatalog(geparst);
      setFehler(geparst ? null : "Die Antwort des Lernraums war nicht lesbar.");
    } catch (ursache) {
      if (aktuelleAnfrage !== anfrageId.current) {
        return;
      }
      setKatalog(null);
      setFehler(describeError(ursache));
    } finally {
      if (aktuelleAnfrage === anfrageId.current) {
        setLaedt(false);
      }
    }
  }, []);

  // Solange ein Download läuft, hält die Karte den Stand aktuell. Ohne Download
  // passiert nichts - die Karte pollt nicht auf Vorrat.
  useEffect(() => {
    if (!katalog?.models.some((model) => model.status.status === "downloading")) {
      return;
    }
    const zeitgeber = window.setInterval(() => void laden(), POLL_INTERVAL_MS);
    return () => window.clearInterval(zeitgeber);
  }, [katalog, laden]);

  useEffect(() => {
    // `react-hooks/set-state-in-effect` (Fehlerregel in eslint-config-next) wertet
    // jeden `setState` im Effektkörper als kaskadierendes Rendern. Der erste Abruf
    // läuft deshalb einen Tick später - dasselbe Muster wie in `lib/use-now.ts`.
    const erster = window.setTimeout(() => void laden(), 0);
    return () => {
      window.clearTimeout(erster);
      anfrageId.current += 1;
    };
  }, [laden]);

  const aktion = useCallback(
    async (model: ModelPayload, art: "download" | "cancel" | "delete") => {
      setPendingAction(`${art}:${model.id}`);
      try {
        if (art === "delete") {
          await apiRequest(`/v1/models/${encodeURIComponent(model.id)}`, { method: "DELETE" });
        } else {
          await apiRequest(`/v1/models/${encodeURIComponent(model.id)}/${art}`, { method: "POST" });
        }
        await laden();
      } catch (ursache) {
        setFehler(describeError(ursache));
      } finally {
        setPendingAction((aktuell) => (aktuell === `${art}:${model.id}` ? null : aktuell));
      }
    },
    [laden],
  );

  const modelle = katalog?.models ?? [];
  const speicher = katalog?.storage;
  const gruppen = modelle.reduce<{ task: ModelPayload["task"]; modelle: ModelPayload[] }[]>((liste, model) => {
    const gruppe = liste.find((eintrag) => eintrag.task === model.task);
    if (gruppe) {
      gruppe.modelle.push(model);
    } else {
      liste.push({ task: model.task, modelle: [model] });
    }
    return liste;
  }, []);

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-3 space-y-0">
        <div className="min-w-0 space-y-1">
          <CardTitle>Lokale Modelle</CardTitle>
          <CardDescription>
            Direkt auf den Server laden. Der Download läuft im Hintergrund und setzt nach einem Abbruch beim Teilstand fort.
          </CardDescription>
        </div>
        <Button type="button" size="sm" variant="outline" onClick={() => void laden()} disabled={laedt}>
          <RefreshCw className={cn(laedt && "animate-spin")} aria-hidden="true" />
          Aktualisieren
        </Button>
      </CardHeader>

      <CardContent className="space-y-4">
        {fehler ? (
          <p className="border border-destructive/20 bg-destructive/10 px-4 py-3 text-sm font-medium text-destructive" role="alert">
            {fehler}
          </p>
        ) : null}

        {speicher && !speicher.writable ? (
          <p className="flex items-start gap-2 border border-foreground/25 bg-foreground/10 px-4 py-3 text-sm font-semibold">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
            {speicher.message}
          </p>
        ) : null}

        {laedt && !katalog ? (
          <div className="space-y-3">
            <Skeleton className="h-4 w-48" />
            <Skeleton className="h-28 w-full" />
          </div>
        ) : null}

        {!laedt && modelle.length === 0 && !fehler ? (
          <p className="text-sm text-muted-foreground">Der Katalog enthält zurzeit kein Modell.</p>
        ) : null}

        {/*
          Nach Aufgabe gruppiert und als Kacheln nebeneinander: ein Textmodell
          und ein Spracherkennungsmodell sind keine Alternativen, und in einer
          einzigen Liste untereinander musste man die Wahl erst zusammensuchen.
        */}
        {gruppen.map((gruppe) => (
          <section key={gruppe.task} className="space-y-2" aria-label={describeModelTask(gruppe.task)}>
            <h4 className="text-xs font-medium text-muted-foreground">{describeModelTask(gruppe.task)}</h4>
            <ul className="grid gap-3 md:grid-cols-2 2xl:grid-cols-3">
              {gruppe.modelle.map((model) => (
                <ModelRow
                  key={model.id}
                  model={model}
                  storageFreeBytes={speicher?.free_bytes ?? null}
                  pendingAction={pendingAction}
                  onStart={(ziel) => void aktion(ziel, "download")}
                  onCancel={(ziel) => void aktion(ziel, "cancel")}
                  onDelete={(ziel) => void aktion(ziel, "delete")}
                />
              ))}
            </ul>
          </section>
        ))}

        {speicher?.message ? <p className="text-[11px] leading-4 text-muted-foreground">{speicher.message}</p> : null}
      </CardContent>
    </Card>
  );
}
