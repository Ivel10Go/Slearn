"use client";

import { useCallback } from "react";
import Link from "next/link";
import { Mic, MicOff, RefreshCw, Square, Volume2, VolumeX } from "lucide-react";
import { Button } from "@/components/ui/button";
import { KUERZEL_BEZEICHNUNG, KUERZEL_BEZEICHNUNG_MAC, beschreibeAufnahme, istMac, sprachHinweis } from "@/lib/speech";
import { useSpeech } from "@/lib/use-speech";
import { cn } from "@/lib/utils";

/*
 * Spracheingabe und Sprachausgabe an der Lernendenkarte - mit dem Textmodus als
 * Normalzustand.
 *
 * Vier Entscheidungen, die man hier nicht wegdiskutieren kann:
 *
 * 1. **Der Zustand steht als Wort, nicht als Farbe.** Das Layout ist monochrome
 *    und vollstaendig eckig, alle `--radius*`-Tokens stehen auf `0px`, und es
 *    gibt bewusst keine Farbklassen, keine Verlaeufe, keine Schatten und kein
 *    `backdrop-blur`. Ein „Aufnahme laeuft"-Indikator ohne rote Farbe braucht
 *    darum etwas anderes: ein **unterstrichenes** Wort, das zusaetzlich
 *    vorgelesen wird. Kein blinkender Kreis - der macht aus einer laeuftden
 *    Aufnahme einen Dauerzustand, den niemand mehr bewusst sieht.
 * 2. **Der Knopf ist der Weg, das Tastenkuerzel der Zusatz.** Push-to-Talk ueber
 *    die Tastatur ist angenehm, setzt aber voraus, dass jemand die Taste kennt.
 *    Wer sie nicht kennt, verliert nichts: der Knopf tut dasselbe.
 * 3. **Kein Fehlerdialog, wo eine Frage noetig waere.** Fehlendes Mikrofon,
 *    abgelehnte Berechtigung, nicht geladenes Modell - drei Faelle, drei Saetze in
 *    der Karte, alle ohne `role="alert"` und ohne roten Rand. Die Karte fragt
 *    nicht, ob es weitergehen soll: es geht immer weiter.
 * 4. **Die Sprachausgabe ist ein Knopf, kein Automatismus.** Es gibt keinen
 *    Pfad, auf dem eine Antwort von allein zu lesen beginnt; jedes Vorlesen
 *    kommt von einer Nutzergeste. Damit stellt sich die Autoplay-Frage gar nicht
 *    erst - und wenn ein Browser sie trotzdem durchsetzt, ist der Knopf da und
 *    der Satz sagt warum.
 *
 * Der Knopf reagiert auf `pointerdown`/`pointerup` und **nicht** auf `click`:
 * Push-to-Talk heisst „solange gedrueckt". `click` wuerde erst beim Loslassen
 * kommen und damit genau das Falsche messen. Die Tastatur laeuft ueber
 * `keydown`/`keyup` - ein natives `<button>` erzeugt fuer Enter und die
 * Leertaste zwar ein `click`, aber kein `pointerdown`, deshalb sind es zwei
 * Wege statt einem.
 */

export interface SpeechControlsProps {
  /** Wohin das Transkript geschrieben wird - in das Fragefeld der Lernendenkarte. */
  onText: (text: string) => void;
  /** Der Text, der vorgelesen werden kann. Leer heisst: nichts da zum Vorlesen. */
  antwort?: string;
}

export function SpeechControls({ onText, antwort }: SpeechControlsProps) {
  const sprache = useSpeech();
  const aufgenommen = Boolean(sprache.transkript.trim());
  const hatAntwort = Boolean(antwort?.trim());
  const laeuft = sprache.aufnahmeZustand === "nimmt_auf";
  const wartet = sprache.aufnahmeZustand === "wird_gesendet" || sprache.laedt;
  const tastenname =
    typeof navigator !== "undefined" && istMac(navigator.userAgent ?? "") ? KUERZEL_BEZEICHNUNG_MAC : KUERZEL_BEZEICHNUNG;

  /** Schreibt das Transkript ins Fragefeld und raeumt die Aufnahme auf. */
  const uebernehmen = useCallback(() => {
    const text = sprache.transkript.trim();
    if (!text) {
      return;
    }
    onText(text);
    sprache.abbrechen();
  }, [onText, sprache]);

  return (
    <div className="space-y-2.5 border-t border-border/70 pt-3">

      {/*
        Die Live-Region steht vor dem Text im DOM, sonst wird er nicht
        angesagt. Sie ist ein `status` und **kein** `alert`: eine abgelehnte
        Berechtigung ist eine gueltige Antwort und unterbricht niemanden.
      */}
      <div role="status" aria-live="polite" className="space-y-1.5">
        <p className={cn("inline-flex items-center gap-2 text-xs font-medium [&_svg]:size-3.5", laeuft && "underline decoration-2 underline-offset-4")}>
          {wartet ? <RefreshCw className="animate-spin" aria-hidden="true" /> : laeuft ? <Square aria-hidden="true" /> : <Mic aria-hidden="true" />}
          <span>{beschreibeAufnahme(sprache.aufnahmeZustand, sprache.sekunden)}</span>
        </p>
        {laeuft ? <p className="text-xs leading-5 text-muted-foreground">Zum Beenden loslassen oder auf &bdquo;Aufnahme beenden&ldquo; tippen.</p> : null}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          size="sm"
          variant={laeuftdeFlagge(laeuft)}
          onPointerDown={(ereignis) => {
            ereignis.preventDefault();
            sprache.aufnehmen();
          }}
          onPointerUp={sprache.beenden}
          onPointerLeave={() => (laeuft ? sprache.beenden() : undefined)}
          onPointerCancel={() => (laeuft ? sprache.beenden() : undefined)}
          onKeyDown={(ereignis) => {
            if (ereignis.key !== "Enter" && ereignis.key !== " ") {
              return;
            }
            ereignis.preventDefault();
            sprache.aufnehmen();
          }}
          onKeyUp={(ereignis) => {
            if (ereignis.key !== "Enter" && ereignis.key !== " ") {
              return;
            }
            ereignis.preventDefault();
            sprache.beenden();
          }}
          disabled={wartet || !sprache.kannEingabe}
          aria-pressed={laeuft}
          aria-describedby="sprache-kuerzel-hinweis"
        >
          {laeuft ? <Square aria-hidden="true" /> : <Mic aria-hidden="true" />}
          {laeuft ? "Aufnahme beenden" : "Aufnehmen"}
        </Button>

        {aufgenommen ? (
          <Button type="button" size="sm" onClick={uebernehmen}>
            Text uebernehmen
          </Button>
        ) : null}

        {laeuft || wartet ? (
          <Button type="button" size="sm" variant="ghost" onClick={sprache.abbrechen}>
            <MicOff aria-hidden="true" />
            Verwerfen
          </Button>
        ) : null}

        {hatAntwort ? (
          sprache.spielt ? (
            <Button type="button" size="sm" variant="outline" onClick={sprache.schweigen}>
              <VolumeX aria-hidden="true" />
              Vorlesen stoppen
            </Button>
          ) : (
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => sprache.sprechen(antwort ?? "")}
              disabled={!sprache.kannAusgabe}
            >
              <Volume2 aria-hidden="true" />
              Antwort vorlesen
            </Button>
          )
        ) : null}

        <Button type="button" size="sm" variant="ghost" onClick={sprache.neuLaden} aria-label="Sprachfunktionen neu pruefen">
          <RefreshCw aria-hidden="true" />
          Neu pruefen
        </Button>
      </div>

      {aufgenommen ? (
        <div className="rounded-md border border-border px-3 py-2.5">
          <p className="text-[11px] font-medium text-muted-foreground">Erkannter Text</p>
          <p className="mt-1.5 text-sm leading-6">{sprache.transkript}</p>
        </div>
      ) : null}

      {/*
        Der einzige Kasten dieser Karte, der wie ein Fehler aussieht - und er ist
        auch einer: der Server hat die Anfrage abgelehnt. Ein abgelehnter
        Mikrofonzugriff kommt **nicht** hierher, sondern in `hinweis`.
      */}
      {sprache.fehler ? (
        <p className="rounded-md border border-foreground/30 bg-muted px-3 py-2 text-xs font-medium" role="alert">
          {sprache.fehler}
        </p>
      ) : null}

      {/*
        Die Hinweise sind eingeklappt: sie erklaeren, warum etwas nicht geht,
        und das liest man einmal, nicht bei jedem Blick aufs Dashboard. Das
        Tastenkuerzel bleibt trotzdem im DOM, weil der Aufnahmeknopf per
        `aria-describedby` darauf zeigt.
      */}
      <details className="group text-[11px] leading-4 text-muted-foreground">
        <summary className="cursor-pointer select-none hover:text-foreground">Hinweise zur Sprache</summary>
        <div className="mt-2 space-y-1.5">
          <p>{sprachHinweis(sprache.sprechZustand)}</p>
          {/*
            Das Tastenkuerzel steht als Satz da, nicht in einem Tooltip: ein
            Kuerzel, das niemand kennt, ist keines. Gewaehlt ist
            `Strg`/`Befehl` + `Umschalt` + `L`, weil `Strg+L` das Adressfeld waere,
            `Esc` und die Leertaste heikel sind und `Alt` in Windows die
            Menueklammer aendert. `lib/speech.ts` prueft zusaetzlich, ob gerade in
            einem Eingabefeld getippt wird, und laesst es dann in Ruhe.
          */}
          <p id="sprache-kuerzel-hinweis" className="text-[11px] leading-4 text-muted-foreground">
            Zum Diktieren {tastenname} halten. Aufgezeichnet wird nur, wenn gerade kein Eingabefeld den Fokus hat.
          </p>

          {/*
            Der Mikrofonhinweis ist ein Satz und kein Dialog. Er steht auch dann da,
            wenn gar nichts passiert ist - dann sagt er, dass getippt werden kann.
          */}
          <p className="text-[11px] leading-4 text-muted-foreground">{sprache.hinweis}</p>

          {/*
            Das Formatproblem der Aufnahme, **bevor** es auftritt. Chrome nimmt WebM
            auf, Safari MP4, und whisper.cpp liest von beidem nichts - der Weg
            darueber laeuft ueber ffmpeg. Steht das Programm nicht auf dem Server,
            ist das kein Fehler, sondern eine Angabe, die man kennen sollte, bevor man
            drei Minuten lang aufnimmt.
          */}
          {sprache.faehigkeiten?.speech_to_text.available && sprache.faehigkeiten.runtime.ffmpeg_present === false ? (
            <p className="text-[11px] leading-4 text-muted-foreground">{sprache.faehigkeiten.runtime.ffmpeg_note}</p>
          ) : null}

          {/*
            Die beiden Gruende kommen vom Server und sind ebenfalls Saetze. Sie
            stehen nur da, wenn sie etwas sagen, das der allgemeine Hinweis nicht
            schon sagt - sonst waere es dieselbe Information zweimal.
          */}
          <div className="space-y-1">
            {!sprache.kannEingabe && sprache.grundEingabe ? <p className="text-[11px] leading-4 text-muted-foreground">Eingabe: {sprache.grundEingabe}</p> : null}
            {!sprache.kannAusgabe && sprache.grundAusgabe ? <p className="text-[11px] leading-4 text-muted-foreground">Ausgabe: {sprache.grundAusgabe}</p> : null}
          </div>
          <p>
            Modelle und Stimmen richtest du unter{" "}
            <Link href="/ki" className="underline underline-offset-2 hover:text-foreground">
              KI &amp; Modelle
            </Link>{" "}
            ein.
          </p>
        </div>
      </details>
    </div>
  );
}

/**
 * Der Knopf sieht beim Halten aus wie ein Knopf, der gedrueckt wird - und sonst
 * wie einer, der es nicht tut.
 *
 * Kein ``variant="destructive"``: Rot waere hier eine Farbe fuer einen Zustand,
 * den es nicht gibt. Der Unterschied ist ein Fuellgrad, nicht ein Warnsignal.
 */
function laeuftdeFlagge(laeuft: boolean): "default" | "outline" {
  return laeuft ? "default" : "outline";
}