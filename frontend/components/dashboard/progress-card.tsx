"use client";

import { useState, type FormEvent } from "react";
import { Flame, RefreshCw, Target, Timer } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { FormError } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  faelligeThemen,
  intervallRegel,
  laufendeSessionHinweis,
  lernzeit,
  streakLabel,
  unBewerteteAnzahl,
  weekdayBalken,
  weekdayKurz,
  wiederholungLabel,
  wochenzielLabel,
} from "@/lib/progress";
import { useProgress } from "@/lib/use-progress";
import type { ConfidenceStep, TopicProgress } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * Die Fortschrittskarte des Dashboards.
 *
 * Sie steht hier und nicht auf ``/lernen``, weil sie eine Frage beantwortet,
 * die nur der Tagesueberblick stellen kann: *was steht heute an?* Konfidenz und
 * Termin sind ein Teil davon, genauso wie Serie und Wochenziel. Eine Karte, die
 * Konfidenz **aendert**, gehoerte in die Verwaltung - deshalb steht das
 * Auswahlfeld nur bei den Themen, die ohnehin dran sind, und die Auswahl der
 * Fuenf Stufen kommt als Vertrag aus derselben Antwort, nach der das Backend
 * rechnet.
 *
 * Die Gestaltung folgt der Regel des Projekts: eckig, monochrom, Haarlinien.
 * Ein Balken ohne Rundung, ein Label in deutscher Sprache, und Zustaende
 * unterscheiden sich ueber Gewicht und Unterstreichung - nicht ueber Farbe.
 */

/** Ein Balken ohne Rundung, mit den aria-Werten, die `role="progressbar"` braucht. */
function Balken({ anteil, label, className }: { anteil: number; label: string; className?: string }) {
  const wert = Math.min(100, Math.max(0, anteil));
  return (
    <div className={cn("h-1.5 w-full overflow-hidden bg-muted", className)} role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={wert}>
      <div className="h-full bg-primary" style={{ width: `${wert}%` }} />
    </div>
  );
}

/** Eine Kachel mit Zahl und Unterschrift - ohne Rahmen, die Zahlen tragen. */
function Kennzahl({ titel, wert, zusatz, balken }: { titel: string; wert: string; zusatz: string; balken?: number }) {
  return (
    <div className="min-w-0">
      <p className="text-[11px] font-medium text-muted-foreground">{titel}</p>
      <p className="mt-1 truncate text-base font-semibold tracking-tight">{wert}</p>
      <p className="mt-0.5 text-xs text-muted-foreground">{zusatz}</p>
      {balken === undefined ? null : <Balken anteil={balken} label={`${titel}: ${wert}`} className="mt-2" />}
    </div>
  );
}

function WochenzielFormular({ ziel, laeuft, onSpeichern }: { ziel: number; laeuft: boolean; onSpeichern: (minuten: number) => void }) {
  const [offen, setOffen] = useState(false);
  const [wert, setWert] = useState(String(ziel));
  const [fehler, setFehler] = useState<string | null>(null);

  function absenden(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const minuten = Number(wert);
    // Dieselben Grenzen wie im Backend. Sie hier zu pruefen ist keine
    // Doppelung: ohne die Meldung vor dem Absenden faellt der Unterschied
    // zwischen "leer" und "unmoeglich" beim Nutzer weg.
    if (!Number.isInteger(minuten) || minuten < 15 || minuten > 20160) {
      setFehler("Das Wochenziel muss zwischen 15 und 20160 Minuten liegen.");
      return;
    }
    setFehler(null);
    onSpeichern(minuten);
    setOffen(false);
  }

  if (!offen) {
    return (
      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" size="sm" variant="ghost" onClick={() => setOffen(true)}>
          <Target className="h-4 w-4" aria-hidden="true" />
          Wochenziel anpassen
        </Button>
      </div>
    );
  }

  return (
    <form onSubmit={absenden} noValidate className="space-y-2 border border-border p-3">
      <label htmlFor="fortschritt-wochenziel" className="block text-xs font-semibold">
        Wochenziel in Minuten
      </label>
      <div className="flex flex-wrap items-end gap-2">
        <Input
          id="fortschritt-wochenziel"
          type="number"
          min={15}
          max={20160}
          step={15}
          value={wert}
          onChange={(event) => setWert(event.target.value)}
          disabled={laeuft}
          className="w-40"
        />
        <Button type="submit" size="sm" disabled={laeuft}>
          Speichern
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={() => setOffen(false)} disabled={laeuft}>
          Abbrechen
        </Button>
      </div>
      <p className="text-xs leading-5 text-muted-foreground">
        Gilt von Montag bis Sonntag für die ganze Woche, nicht anteilig für die Tage, die schon vorbei sind.
      </p>
      <FormError message={fehler} />
    </form>
  );
}

function ThemenZeile({
  thema,
  stufen,
  laeuft,
  onBewerten,
}: {
  thema: TopicProgress;
  stufen: ConfidenceStep[];
  laeuft: boolean;
  onBewerten: (topicId: number, confidence: number) => void;
}) {
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-2 border border-border bg-card/60 px-3 py-2.5">
      <div className="min-w-0 flex-1">
        <p className={cn("truncate text-sm font-semibold", thema.is_due && "underline underline-offset-4")}>{thema.name}</p>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {[thema.subject_name, wiederholungLabel(thema)].filter(Boolean).join(" · ")}
        </p>
      </div>
      {thema.studied_minutes > 0 ? (
        <span className="shrink-0 text-xs font-semibold text-muted-foreground">{lernzeit(thema.studied_minutes)} gelernt</span>
      ) : null}
      <Badge variant={thema.is_due ? "default" : "outline"}>{thema.is_due ? "Fällig" : thema.confidence_label}</Badge>
      <label className="sr-only" htmlFor={`fortschritt-thema-${thema.topic_id}`}>
        Wie sicher fühlst du dich bei {thema.name}?
      </label>
      <Select
        id={`fortschritt-thema-${thema.topic_id}`}
        // Die **Stufe**, nicht der Rohwert: der kann zwischen zwei Stufen
        // liegen (etwa 0.9 aus einem API-Aufruf) und waere hier kein Wert, den
        // man anwaehlen kann. Angezeigt wird, was das Intervall bestimmt.
        value={String(thema.confidence_step)}
        disabled={laeuft}
        onChange={(event) => onBewerten(thema.topic_id, Number(event.target.value))}
        className="h-9 w-48 shrink-0 text-xs"
      >
        {stufen.map((stufe) => (
          <option key={stufe.value} value={stufe.value}>
            {stufe.label} · {stufe.interval_days} {stufe.interval_days === 1 ? "Tag" : "Tage"}
          </option>
        ))}
      </Select>
    </li>
  );
}

export function ProgressCard({ refreshKey = 0 }: { refreshKey?: number }) {
  const { insights, topics, status, error, actionError, pending, reload, bewerten, wochenzielSetzen } = useProgress(refreshKey);
  const faellig = faelligeThemen(topics);
  const unbewertet = unBewerteteAnzahl(topics);

  if (status === "loading" && !insights) {
    return (
      <Card>
        <CardHeader className="pb-4">
          <CardTitle>Lernfortschritt</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3" aria-busy="true" aria-label="Lernfortschritt wird geladen">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-32 w-full" />
        </CardContent>
      </Card>
    );
  }

  if (!insights) {
    return (
      <Card role="alert">
        <CardHeader className="pb-4">
          <CardTitle>Lernfortschritt</CardTitle>
        </CardHeader>
        <CardContent>
          <Button type="button" size="sm" variant="outline" onClick={() => void reload()}>
            <RefreshCw aria-hidden="true" />
            Erneut versuchen
          </Button>
        </CardContent>
      </Card>
    );
  }

  const woche = insights.week;
  const laufend = laufendeSessionHinweis(insights);

  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-3 space-y-0 pb-3">
        <div>
          <CardTitle>Lernfortschritt</CardTitle>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant={insights.streak.current_days > 0 ? "default" : "secondary"}>
            {insights.streak.current_days > 0 ? <Flame className="h-3.5 w-3.5" aria-hidden="true" /> : null}
            {streakLabel(insights.streak)}
          </Badge>
          <Button type="button" size="icon" variant="ghost" onClick={() => void reload()} aria-label="Lernfortschritt neu laden" disabled={status === "loading"}>
            <RefreshCw className={cn(status === "loading" && "animate-spin")} aria-hidden="true" />
          </Button>
        </div>
      </CardHeader>

      <CardContent className="space-y-4">
        {error ? (
          <p className="border border-destructive/20 bg-destructive/10 px-3 py-2 text-sm text-destructive" role="alert">
            {error}
          </p>
        ) : null}
        <FormError message={actionError} />

        <div className="grid gap-4 sm:grid-cols-3">
          <Kennzahl
            titel="Heute"
            wert={lernzeit(insights.today.minutes)}
            zusatz={insights.today.sessions === 1 ? "1 Lernsession" : `${insights.today.sessions} Lernsessions`}
          />
          <Kennzahl
            titel="Diese Woche"
            wert={wochenzielLabel(woche)}
            zusatz={woche.goal_reached ? "Ziel erreicht" : `noch ${woche.days_remaining} ${woche.days_remaining === 1 ? "Tag" : "Tage"}`}
            balken={woche.percent}
          />
          <Kennzahl
            titel="Serie"
            wert={`${insights.streak.current_days} ${insights.streak.current_days === 1 ? "Tag" : "Tage"}`}
            zusatz={insights.streak.current_days > 0 ? `längste ${insights.streak.longest_days} Tage` : streakLabel(insights.streak)}
          />
        </div>

        {laufend ? (
          <p className="flex items-start gap-2 border border-border bg-muted/40 px-3 py-2 text-xs leading-5 text-muted-foreground">
            <Timer className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
            {laufend}
          </p>
        ) : null}

        {/*
          Die Wochentagsverteilung. Sieben gleich breite Spalten mit einer
          Haarlinie als Schiene - ein Balken je Tag, ohne Rundung und ohne
          Farbe. Der Betrachtungszeitraum steht in der Unterschrift, weil ein
          Balken ohne Zeitraum eine Behauptung waere.
        */}
        <div>
          <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
            <h3 className="text-xs font-medium text-muted-foreground">Lernzeit nach Wochentag</h3>
            <p className="text-xs text-muted-foreground">Letzte {insights.weekday_window_days} Tage</p>
          </div>
          <div className="flex items-end gap-2">
            {weekdayBalken(insights.by_weekday).map((tag) => (
              <div key={tag.weekday} className="flex min-w-0 flex-1 flex-col items-center gap-1.5">
                <span className="text-[11px] font-semibold tabular-nums text-muted-foreground">{tag.minutes > 0 ? tag.minutes : "–"}</span>
                {/*
                  Die Schiene ist die Haarlinie, der Balken darin. Die
                  aria-Werte nennen die echten Minuten gegen den groessten
                  Wochentag - nicht den Balkenanteil, sonst meldet ein
                  Screenreader eine Prozentzahl, die nicht zur Lernzeit gehoert.
                  Der Mindestwert von 1 haelt den Balken benutzbar, wenn noch
                  nie gelernt wurde und es nichts zu vergleichen gibt.
                */}
                <div
                  className="flex h-20 w-full items-end border border-border bg-muted/40"
                  role="progressbar"
                  aria-label={`${tag.label}: ${lernzeit(tag.minutes)}`}
                  aria-valuemin={0}
                  aria-valuemax={Math.max(1, tag.maximum)}
                  aria-valuenow={tag.minutes}
                >
                  {/* Ein Tag mit Lernzeit behaelt eine sichtbare Mindesthoehe, sonst waere er leer. */}
                  <div className="w-full bg-primary" style={{ height: `${Math.max(tag.anteil, tag.minutes > 0 ? 6 : 0)}%` }} />
                </div>
                <span className="text-[11px] font-medium text-muted-foreground">{weekdayKurz(tag.label)}</span>
              </div>
            ))}
          </div>
        </div>

        {insights.by_subject.length > 0 ? (
          <div>
            {/*
              Die Gesamtsumme steht hier und nicht beim Wochentagsbalken: die
              Verteilung dort ist ein Fenster von zwoelf Wochen, die Summe
              nicht. Beides aneinander zu schreiben wuerde beides falsch.
            */}
            <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
              <h3 className="text-xs font-medium text-muted-foreground">Lernzeit je Fach</h3>
              <p className="text-xs text-muted-foreground">
                {lernzeit(insights.total_minutes)} aus {insights.total_sessions} Lernsessions gesamt
              </p>
            </div>
            <ul className="space-y-2.5">
              {insights.by_subject.map((fach) => (
                <li key={fach.subject_id ?? "ohne-fach"}>
                  <div className="flex items-baseline justify-between gap-3">
                    <p className="truncate text-sm font-semibold">{fach.subject_name}</p>
                    <p className="shrink-0 text-xs font-semibold tabular-nums text-muted-foreground">
                      {lernzeit(fach.minutes)} · {fach.share_percent.toLocaleString("de-DE", { maximumFractionDigits: 1 })} %
                    </p>
                  </div>
                  <Balken anteil={fach.share_percent} label={`${fach.subject_name}: ${lernzeit(fach.minutes)}`} className="mt-1" />
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        <div>
          <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
            <h3 className="text-xs font-medium text-muted-foreground">Nächste Wiederholungen</h3>
            <p className="text-xs text-muted-foreground">
              {faellig.length > 0 ? `${faellig.length} fällig` : "Nichts fällig"}
              {unbewertet > 0 ? ` · ${unbewertet} noch nicht eingeschätzt` : ""}
            </p>
          </div>

          {topics.length === 0 ? (
            <EmptyState
              icon={Target}
              title="Noch keine Themen"
              description="Sobald ein Fach Themen hat, steht hier, was als Nächstes wiederholt wird. Das Datum ergibt sich aus deiner Einschätzung."
            />
          ) : (
            <ul className="max-h-96 space-y-2 overflow-y-auto pr-1">
              {topics.map((thema) => (
                <ThemenZeile
                  key={thema.topic_id}
                  thema={thema}
                  stufen={insights.confidence_steps}
                  laeuft={pending !== null}
                  onBewerten={(topicId, confidence) => void bewerten(topicId, confidence)}
                />
              ))}
            </ul>
          )}

          {/*
            Die Regel steht unter der Liste, nicht in einem Tooltip: sie ist der
            Grund, warum es hier ueberhaupt Termine gibt. Ohne sie sieht das
            Auswahlfeld nach einer Punktbewertung aus. Der Text entsteht aus
            derselben Tabelle, nach der das Backend rechnet.
          */}
          <p className="mt-3 text-xs leading-5 text-muted-foreground">
            {intervallRegel(insights.confidence_steps)} Die Einschätzung zählt zugleich als Wiederholung — deshalb steht das Datum auf heute.
          </p>
        </div>

        <WochenzielFormular
          // `key` statt eines Effekts: ein neues Ziel aus dem Server setzt das
          // Feld zurueck, ohne dass ein Zustandswechsel in einem Effekt noetig
          // waere.
          key={insights.week.goal_minutes}
          ziel={insights.week.goal_minutes}
          laeuft={pending === "goal"}
          onSpeichern={(minuten) => void wochenzielSetzen(minuten)}
        />
      </CardContent>
    </Card>
  );
}
