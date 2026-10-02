"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, Download, LoaderCircle, RefreshCw, Trash2, Volume2, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cancelVoiceDownload, deleteVoice, downloadVoice, getSpeechCapabilities, getSpeechVoices } from "@/lib/api";
import { formatModelBytes } from "@/lib/models";
import {
  brauchtDeutscheStimme,
  beschreibeQualitaet,
  beschreibeStimmstatus,
  type VoiceListPayload,
  type VoicePayload,
} from "@/lib/speech";
import { cn } from "@/lib/utils";

/*
 * Die Piper-Stimmen unter Einstellungen.
 *
 * Die Karte steht bewusst **nicht** beim allgemeinen Modellkatalog, obwohl sie
 * denselben Ablageort und denselben Download-Manager benutzt. Der Grund ist der
 * Unterschied im Katalogeintrag: eine Whisper-Datei hat **eine** Sprache und
 * **keine** Stimme, eine Piper-Stimme ist **eine** Stimme **in einer** Sprache.
 * Dazu kommen zwei Dateien, Abtastrate und Lizenz der Stimmdaten. Ein Listen­
 * eintrag im Textmodell-Katalog wuerde beides in eine Zeile pressen und damit
 * genau die Entscheidung unsichtbar machen, die hier sichtbar sein soll.
 *
 * Die Sprachauswahl ist damit an beiden Stellen behandelt: **hier** waehlt man
 * aus, **dort** (in der Sprachkarte) wird nur noch benutzt, was geladen ist.
 *
 * Drei Punkte, die man beim Design nicht wegdiskutieren kann:
 *
 * 1. **Sprache und Stimme stehen getrennt.** Jede Zeile nennt die Sprache
 *    (`language_label`), den Sprecher (`speaker_label`) und die Qualitaet als
 *    eigene Beschriftungen. „Deutsche Stimme" und „diese Stimme" sind zwei
 *    Entscheidungen - in einer Spalte waeren sie eine.
 * 2. **Eine geladene englische Stimme ist keine Alternative.** Fehlt eine
 *    deutsche, steht das als ausdruecklicher Satz da, statt die geladene
 *    Stimme einfach vorzuschlagen.
 * 3. **Der Fortschrittsbalken ist schwarz auf grau**, wie in der Modellkarte.
 *    Es gibt keine Farbklassen und keine Verlaeufe; der Zustand steht als
 *    deutsches Wort daneben.
 */

const POLL_INTERVAL_MS = 2000;

/** Wie viele Zeilen mit „Entfernen" bestaetigt werden. Eine, und genau diese. */
function stimmenNachSprache(voices: VoicePayload[]) {
  const deutsch = voices.filter((stimme) => stimme.language === "de_DE");
  const andere = voices.filter((stimme) => stimme.language !== "de_DE");
  return [
    { sprache: "Deutsch", gruppe: deutsch },
    { sprache: "Andere Sprachen", gruppe: andere },
  ].filter((abschnitt) => abschnitt.gruppe.length > 0);
}

function Fehlermeldung(ursache: unknown) {
  if (ursache instanceof Error && ursache.message) {
    return ursache.message;
  }
  return "Die Stimmen konnten nicht geladen werden.";
}

function VoiceRow({
  stimme,
  vorschlag,
  wartet,
  onStart,
  onCancel,
  onDelete,
}: {
  stimme: VoicePayload;
  vorschlag: boolean;
  wartet: boolean;
  onStart: (stimme: VoicePayload) => void;
  onCancel: (stimme: VoicePayload) => void;
  onDelete: (stimme: VoicePayload) => void;
}) {
  const [bestaetigen, setBestaetigen] = useState(false);
  const laeuft = stimme.status.status === "downloading";
  const geladen = stimme.status.status === "installed" && stimme.status.verified;
  const anteil = Math.min(100, Math.max(0, stimme.status.progress_percent));

  return (
    <li className="flex flex-col rounded-md border border-border/70 p-3.5">
      <div className="flex items-start justify-between gap-3">
        <p className="min-w-0 text-[13px] font-semibold leading-5">{stimme.label}</p>
        <Badge variant={geladen ? "success" : "secondary"} className="shrink-0">
          {beschreibeStimmstatus(stimme.status.status)}
        </Badge>
      </div>
      <p className="mt-1 flex flex-wrap gap-x-2 text-xs text-muted-foreground">
        <span>{stimme.speaker_label}</span>
        <span aria-hidden="true">·</span>
        <span>{beschreibeQualitaet(stimme.quality)}</span>
        <span aria-hidden="true">·</span>
        <span className="tabular-nums">{formatModelBytes(stimme.size_bytes)}</span>
        {vorschlag ? (
          <>
            <span aria-hidden="true">·</span>
            <span className="font-medium text-foreground">Vorschlag des Lernraums</span>
          </>
        ) : null}
      </p>

      <p className="mt-2 text-xs leading-5 text-muted-foreground">{stimme.description}</p>

      {/*
        Zwei Dateien, nicht eine: das ONNX-Modell und seine JSON-Konfiguration.
        Piper braucht beide - ohne die Konfiguration kennt es weder Abtastrate
        noch Phoneme. Die technischen Angaben sind eingeklappt: sie sind Beleg,
        nicht der Grund, eine Stimme zu waehlen.
      */}
      <details className="mt-2 text-[11px] text-muted-foreground">
        <summary className="cursor-pointer select-none hover:text-foreground">Technische Angaben</summary>
        <dl className="mt-1.5 space-y-0.5">
          <div className="flex justify-between gap-3">
            <dt>Abtastrate</dt>
            <dd className="tabular-nums">{stimme.sample_rate.toLocaleString("de-DE")} Hz</dd>
          </div>
          <div className="flex justify-between gap-3">
            <dt>Lizenz der Stimmdaten</dt>
            <dd className="truncate">{stimme.license}</dd>
          </div>
        </dl>
        <ul className="mt-1.5 space-y-0.5">
          {stimme.files.map((datei) => (
            <li key={datei.name} className="flex items-baseline justify-between gap-3">
              <span className="truncate font-mono">{datei.name}</span>
              <span className="shrink-0 tabular-nums">
                {datei.is_model ? "Modell" : datei.is_config ? "Konfiguration" : "Datei"} · {formatModelBytes(datei.size_bytes)}
              </span>
            </li>
          ))}
        </ul>
      </details>

      {laeuft || stimme.status.status !== "not_downloaded" ? (
        <div className="mt-2.5">
          <div
            className="h-2 w-full border border-border bg-muted"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(anteil)}
            aria-valuetext={`${anteil.toFixed(0)} Prozent geladen`}
            aria-label={`Ladefortschritt von ${stimme.label}`}
          >
            <div className={cn("h-full bg-foreground", laeuft ? "opacity-70" : "opacity-100")} style={{ width: `${anteil}%` }} />
          </div>
        </div>
      ) : null}

      {/* Wie in der Modellkarte: der Container steht immer da, nur sein Inhalt wechselt. */}
      <div className={cn(stimme.status.message && "mt-2")} role="status" aria-live="polite">
        {stimme.status.message ? <p className="text-xs leading-5 text-muted-foreground">{stimme.status.message}</p> : null}
      </div>

      <div className="mt-auto flex flex-wrap items-center gap-2 pt-3">
        {!laeuft ? (
          <Button type="button" size="sm" onClick={() => onStart(stimme)} disabled={wartet || geladen}>
            {wartet ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <Download aria-hidden="true" />}
            {stimme.status.status === "not_downloaded" ? "Herunterladen" : "Fortsetzen"}
          </Button>
        ) : (
          <Button type="button" size="sm" variant="outline" onClick={() => onCancel(stimme)}>
            <X aria-hidden="true" />
            Abbrechen
          </Button>
        )}

        {bestaetigen ? (
          <>
            <Button type="button" size="sm" variant="destructive" onClick={() => onDelete(stimme)} disabled={laeuft}>
              <Trash2 aria-hidden="true" />
              Wirklich entfernen
            </Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => setBestaetigen(false)} disabled={laeuft}>
              Abbrechen
            </Button>
          </>
        ) : (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={() => setBestaetigen(true)}
            disabled={laeuft || stimme.status.status === "not_downloaded"}
            aria-expanded={bestaetigen}
          >
            <Trash2 aria-hidden="true" />
            Entfernen
          </Button>
        )}

        <a
          href={stimme.source_page}
          target="_blank"
          rel="noreferrer noopener"
          className="inline-flex h-9 items-center gap-2 border border-transparent px-3.5 text-xs font-semibold text-muted-foreground underline underline-offset-4 hover:bg-muted hover:text-foreground"
        >
          <Volume2 aria-hidden="true" />
          Quelle
        </a>
      </div>
    </li>
  );
}

export function VoiceCatalogCard() {
  const anfrageId = useRef(0);
  const [daten, setDaten] = useState<VoiceListPayload | null>(null);
  const [empfehlung, setEmpfehlung] = useState<string | null>(null);
  const [fehler, setFehler] = useState<string | null>(null);
  const [laedt, setLaedt] = useState(true);
  const [pending, setPending] = useState<string | null>(null);

  const laden = useCallback(async () => {
    const aktuelle = ++anfrageId.current;
    try {
      const antwort = await getSpeechVoices();
      if (aktuelle !== anfrageId.current) {
        return;
      }
      setDaten(antwort);
      setFehler(null);
    } catch (ursache) {
      if (aktuelle !== anfrageId.current) {
        return;
      }
      setDaten(null);
      setFehler(Fehlermeldung(ursache));
    } finally {
      if (aktuelle === anfrageId.current) {
        setLaedt(false);
      }
    }
  }, []);

  /*
    * Die Empfehlung kommt aus der Faehigkeitsabfrage und nicht aus der
    * Stimmenliste: dort steht sie auch dann, wenn nichts geladen ist. Aus der Liste
    * genommen waere sie leer, bis man genau die Zeile heruntergeladen hat, die man
    * wegen dieser leeren Markierung herunterlaedt.
    */
  useEffect(() => {
    const aktuelle = ++anfrageId.current;
    const erster = window.setTimeout(() => {
      void (async () => {
        try {
          const faehigkeiten = await getSpeechCapabilities();
          if (aktuelle === anfrageId.current) {
            setEmpfehlung(faehigkeiten.recommended_voice_id);
          }
        } catch {
          // Ohne Empfehlung steht einfach keine Markierung da - kein Fehler, denn
          // es ist nur eine Auszeichnung, keine Zusage.
        }
      })();
    }, 0);
    return () => {
      window.clearTimeout(erster);
    };
  }, []);

  useEffect(() => {
    // `react-hooks/set-state-in-effect` (Fehlerregel) erlaubt keinen synchronen
    // Zustandswechsel im Effektkoerper: der erste Abruf laeuft einen Tick
    // spaeter, wie in `use-notifications.ts`.
    const erster = window.setTimeout(() => void laden(), 0);
    return () => {
      window.clearTimeout(erster);
      anfrageId.current += 1;
    };
  }, [laden]);

  // Nur waehrend eines Downloads wird gepollt. Ohne Download passiert nichts.
  useEffect(() => {
    if (!daten?.voices.some((stimme) => stimme.status.status === "downloading")) {
      return;
    }
    const zeitgeber = window.setInterval(() => void laden(), POLL_INTERVAL_MS);
    return () => window.clearInterval(zeitgeber);
  }, [daten, laden]);

  const aktion = useCallback(
    async (stimme: VoicePayload, art: "download" | "cancel" | "delete") => {
      setPending(`${art}:${stimme.id}`);
      try {
        if (art === "download") {
          await downloadVoice(stimme.id);
        } else if (art === "cancel") {
          await cancelVoiceDownload(stimme.id);
        } else {
          await deleteVoice(stimme.id);
        }
        await laden();
      } catch (ursache) {
        setFehler(Fehlermeldung(ursache));
      } finally {
        setPending((aktuell) => (aktuell === `${art}:${stimme.id}` ? null : aktuell));
      }
    },
    [laden],
  );

  const stimmen = daten?.voices ?? [];
  const hatDeutsche = brauchtDeutscheStimme(stimmen);

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-3 space-y-0">
        <div className="min-w-0 space-y-1">
          <CardTitle>Stimmen für die Sprachausgabe</CardTitle>
          <CardDescription>
            Eine Stimme ist immer <strong>eine Stimme in einer Sprache</strong>. Für den Lernraum brauchst du eine deutsche -
            jede Stimme ist eine andere Person. Ohne Stimme bleibt alles andere genau so, wie es ist.
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

        {laedt && !daten ? (
          <div className="space-y-3">
            <Skeleton className="h-4 w-48" />
            <Skeleton className="h-28 w-full" />
          </div>
        ) : null}

        {!laedt && stimmen.length === 0 && !fehler ? (
          <p className="text-sm text-muted-foreground">Der Katalog enthält zurzeit keine Stimme.</p>
        ) : null}

        {/*
          Das ist die zweite Haelfte der Sprachauswahl: geladen ist eine Stimme,
          aber keine deutsche. Das wird als Satz gesagt, nicht durch eine
          Vorauswahl einer anderen Sprache zugeteeckt.
        */}
        {!laedt && stimmen.length > 0 && !hatDeutsche ? (
          <p className="flex items-start gap-2 border border-foreground/25 bg-foreground/10 px-4 py-3 text-sm font-semibold">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
            Es ist noch keine deutsche Stimme geladen. Eine Stimme in einer anderen Sprache wird in einer deutschen Oberfläche
            nicht ausgegeben - lade unten eine deutsche nach.
          </p>
        ) : null}

        {!laedt
          ? stimmenNachSprache(stimmen).map((abschnitt) => (
              <section key={abschnitt.sprache} className="space-y-2">
                <h4 className="text-xs font-medium text-muted-foreground">{abschnitt.sprache}</h4>
                <ul className="grid gap-3 md:grid-cols-2 2xl:grid-cols-3">
                  {abschnitt.gruppe.map((stimme) => (
                    <VoiceRow
                      key={stimme.id}
                      stimme={stimme}
                      vorschlag={(daten?.default_voice_id ?? empfehlung) === stimme.id}
                      wartet={pending === `download:${stimme.id}`}
                      onStart={(ziel) => void aktion(ziel, "download")}
                      onCancel={(ziel) => void aktion(ziel, "cancel")}
                      onDelete={(ziel) => void aktion(ziel, "delete")}
                    />
                  ))}
                </ul>
              </section>
            ))
          : null}

        <p className="text-[11px] leading-4 text-muted-foreground">
          Piper läuft als eigenes Programm auf dem Server und lädt die Dateien zur Laufzeit. Fehlt es, bleibt die Spracheingabe
          und der Textmodus unverändert - nur das Vorlesen fällt dann aus.
        </p>
      </CardContent>
    </Card>
  );
}