"use client";

import { Award, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import {
  badgeAnteil,
  badgeBereichLabel,
  badgeBilanz,
  badgeFortschrittLabel,
  badgeHaufen,
  entryArtLabel,
  entryDatumLabel,
  entryPunkteLabel,
  LISTEN_LIMIT,
  punkteLabel,
  regelSatz,
  wochenLabel,
} from "@/lib/rewards";
import { useRewards } from "@/lib/use-rewards";
import type { RewardBadge, RewardEntry, RewardOverview } from "@/lib/types";
import { cn } from "@/lib/utils";
import { t } from "@/lib/i18n";

/**
 * Die Belohnungskarte des Dashboards.
 *
 * **Warum hier und nicht auf `/lernen`.** Die Verwaltung liegt dort, und diese Karte
 * ist keine Verwaltung: sie hat kein Formular, kein Anlegen, kein Loeschen. Sie
 * beantwortet dieselbe Frage wie die Noten- und die Fortschrittskarte - "wie stehe
 * ich heute da?" - und die Punkte aendern sich genau dann, wenn auf dem Dashboard ein
 * Fokusblock endet. Ein Zaehler, der zwei Seiten weiter steht als der Knopf, der ihn
 * veraendert, ist ein Zaehler, den man beim Rennen nicht findet.
 *
 * **Warum das so nackt aussieht.** Das Projekt ist eckig, monochrom und trennt
 * Zustaende ueber Gewicht, Unterstrikung und deutsche Labels. Also: Zahl, Einrueckung,
 * Haarlinie. Keine Medaille, kein Level, kein Toast ueber "+50 Punkte", keine
 * Konfetti-Effekte - die wuerden genau die Sorte System belohnen, die diese Karte
 * eigentlich abwerten soll. Eine Auszeichnung wird **nicht** mit der Badge-Komponente
 * dargestellt; ihr Zustand traegt sich ueber "Erreicht" und eine Unterstrikung.
 *
 * **Eine Anfrage.** `useRewards` ruft genau einen Endpunkt auf, und die Regel kommt
 * mit der Antwort mit. Die Karte baut daraus ihren Text, statt ihn zu erfinden - sonst
 * waere die Erklaerung die zweite Wahrheit ueber die Punkte.
 */

/**
 * Der kurze Grund fuer eine Buchung ohne Punkte.
 *
 * Nur an einer Stelle formuliert: die ausfuehrliche Fassung steht unter der Liste,
 * neben der Zeile reicht dieser Satz. Ohne ihn stuende eine Zeile ohne Punkte da, und
 * die Karte wuerde nach einem Fehler aussehen.
 */
const NULL_PUNKT_KURZ = "Tageskonto war voll";

function Balken({ anteil, label, className }: { anteil: number; label: string; className?: string }) {  const wert = Math.min(100, Math.max(0, anteil));
  return (
    <div className={cn("h-1.5 w-full overflow-hidden bg-muted", className)} role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={wert}>
      <div className="h-full bg-primary" style={{ width: `${wert}%` }} />
    </div>
  );
}

/** Eine Zahl mit ihrer Unterschrift. Kein Rahmen - die Zahlen tragen die Struktur. */
function Kennzahl({ titel, wert, zusatz, balken, balkenLabel }: { titel: string; wert: string; zusatz: string; balken?: number; balkenLabel?: string }) {
  return (
    <div className="min-w-0">
      <p className="text-[11px] font-medium text-muted-foreground">{titel}</p>
      <p className="mt-1 truncate text-base font-semibold tracking-tight">{wert}</p>
      <p className="mt-0.5 text-xs text-muted-foreground">{zusatz}</p>
      {balken === undefined ? null : <Balken anteil={balken} label={balkenLabel ?? titel} className="mt-2" />}
    </div>
  );
}

/**
 * Eine Auszeichnung als Zeile.
 *
 * Erreicht heisst: fett und unterstrichen. Offen heisst: normaler Satz und ein Balken,
 * der den Fuellstand zeigt. Beides steht als deutsches Wort daneben, damit der Zustand
 * nicht nur an einem Farbton haengt, den es hier gar nicht gibt.
 */
function BadgeZeile({ badge }: { badge: RewardBadge }) {
  const anteil = badgeAnteil(badge);
  const bereich = badgeBereichLabel(badge);
  return (
    <li className={cn("border px-3 py-2.5", badge.awarded ? "border-foreground bg-card" : "border-border bg-card/60")}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <p className={cn("min-w-0 truncate text-sm", badge.awarded ? "font-semibold underline underline-offset-4" : "font-semibold")}>
          {badge.title}
        </p>
        <p className="shrink-0 text-xs font-semibold text-muted-foreground">
          {badge.awarded ? t("Erreicht") : badgeFortschrittLabel(badge)}
        </p>
      </div>
      <p className="mt-0.5 text-xs leading-5 text-muted-foreground">
        {[bereich, badge.awarded ? null : badge.description].filter(Boolean).join(" · ")}
      </p>
      {badge.awarded ? null : <Balken anteil={anteil} label={`${badge.title}: ${badgeFortschrittLabel(badge)}`} className="mt-2" />}
      {badge.repeatable && badge.awarded ? (
        <p className="mt-1.5 text-[11px] font-medium text-muted-foreground">{t("Wiederholbar · nächste Woche neu")}</p>
      ) : null}
    </li>
  );
}

/** Eine Buchung als Zeile. Eine Zeile mit 0 Punkten traegt ihren Grund mit. */
function BuchungZeile({ entry, hinweis }: { entry: RewardEntry; hinweis: string }) {
  return (
    <li className="border border-border bg-card/60 px-3 py-2.5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <p className="min-w-0 truncate text-sm font-semibold">
          <span className="font-medium text-muted-foreground">{entryArtLabel(entry)}</span>
          <span className="mx-2 text-border">·</span>
          {entry.detail}
        </p>
        <p className="shrink-0 text-xs font-semibold tabular-nums text-muted-foreground">{entryPunkteLabel(entry.points)}</p>
      </div>
      <p className="mt-0.5 text-xs text-muted-foreground">
        {entryDatumLabel(entry.earned_on)}
        {entry.points === 0 ? <span className="mx-2 text-border">·</span> : null}
        {entry.points === 0 ? <span>{hinweis}</span> : null}
      </p>
    </li>
  );
}

function KartenInhalt({ overview, error }: { overview: RewardOverview; error: string | null }) {
  const { summary, rules, badges, entries } = overview;
  const haufen = badgeHaufen(badges);
  const bilanz = badgeBilanz(overview);
  const heuteAnteil = summary.today_cap > 0 ? Math.round((summary.today_points / summary.today_cap) * 100) : 0;
  const sichtbareEintraege = entries.slice(0, LISTEN_LIMIT);

  return (
    <CardContent className="space-y-4">
      {error ? (
        <p className="border border-destructive/20 bg-destructive/10 px-3 py-2 text-sm text-destructive" role="alert">
          {error}
        </p>
      ) : null}

      <div className="grid gap-4 sm:grid-cols-3">
        <Kennzahl
          titel={t("Heute")}
          wert={`${summary.today_points}`}
          zusatz={t("von {n} Punkten möglich", { n: summary.today_cap })}
          balken={heuteAnteil}
          balkenLabel={t("Punkte heute: {wert}", { wert: punkteLabel(summary) })}
        />
        <Kennzahl titel={t("Diese Woche")} wert={`${summary.week_points}`} zusatz={wochenLabel(summary)} />
        <Kennzahl
          titel={t("Auszeichnungen")}
          wert={`${bilanz.erreicht}`}
          zusatz={t("von {n} im Katalog", { n: bilanz.moeglich })}
        />
      </div>

      {/*
        Das Punktekonto ist eine Liste, keine Zahl. Die Gesamtzahl steht deshalb
        ueber der Liste und nicht in den Kennzahlen: sie ist die Summe ueber
        Eintraege, von denen die Karte nur die naechsten zeigt.
      */}
      <div className="flex flex-wrap items-baseline justify-between gap-2 border-t border-border pt-4">
        <h3 className="text-xs font-medium text-muted-foreground">{t("Insgesamt")}</h3>
        <p className="text-sm font-semibold tabular-nums">{summary.total_points} {t("Punkte")}</p>
      </div>

      <div>
        <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
          <h3 className="text-xs font-medium text-muted-foreground">{t("Auszeichnungen")}</h3>
          <p className="text-xs text-muted-foreground">
            {haufen.erreicht.length > 0 ? t("{n} erreicht", { n: haufen.erreicht.length }) : t("Noch keine erreicht")}
            {haufen.offen.length > 0 ? ` · ${t("{n} offen", { n: haufen.offen.length })}` : ""}
          </p>
        </div>

        {badges.length === 0 ? (
          <EmptyState
            icon={Award}
            title={t("Noch keine Auszeichnungen")}
            description={t("Auszeichnungen entstehen aus deiner Arbeit an Fächern und Themen — nicht aus der Zahl der Klicks. Sie tauchen hier auf, sobald es etwas zu zeigen gibt.")}
          />
        ) : (
          <ul className="space-y-2.5">
            {[...haufen.erreicht, ...haufen.offen].map((badge) => (
              <BadgeZeile key={`${badge.code}:${badge.scope}`} badge={badge} />
            ))}
          </ul>
        )}
      </div>

      <div>
        <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
          <h3 className="text-xs font-medium text-muted-foreground">{t("Zuletzt gutgeschrieben")}</h3>
          <p className="text-xs text-muted-foreground">
            {entries.length > sichtbareEintraege.length ? t("die letzten {n} von {gesamt}", { n: sichtbareEintraege.length, gesamt: entries.length }) : t("die letzten Buchungen")}
          </p>
        </div>

        {sichtbareEintraege.length === 0 ? (
          <p className="border border-dashed border-border bg-muted/30 px-3 py-4 text-sm leading-6 text-muted-foreground">
            {t("Noch nichts gebucht. Punkte gibt es für erledigte Aufgaben mit Thema und für abgeschlossene Lernzeit in einem Fach.")}
          </p>
        ) : (
          <ul className="max-h-80 space-y-2 overflow-y-auto pr-1">
            {sichtbareEintraege.map((entry) => (
              <BuchungZeile key={entry.id} entry={entry} hinweis={NULL_PUNKT_KURZ} />
            ))}
          </ul>
        )}
      </div>

      {/*
        Die Regel steht unter der Liste und nicht in einem Tooltip: sie ist der
        Grund, warum es hier ueberhaupt Punkte gibt. Der Text entsteht aus den
        Zahlen der Antwort, nach denen das Backend rechnet - nicht aus einem
        zweiten Satz im Frontend.
      */}
      <p className="border-t border-border pt-3 text-xs leading-5 text-muted-foreground">{regelSatz(rules, rules.subject_day_steps)}</p>
    </CardContent>
  );
}

export function RewardsCard({ refreshKey = 0 }: { refreshKey?: number }) {
  const { overview, status, error, reload } = useRewards(refreshKey);

  if (status === "loading" && !overview) {
    return (
      <Card>
        <CardHeader className="pb-4">
          <CardTitle>{t("Belohnung")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3" aria-busy="true" aria-label={t("Belohnung wird geladen")}>
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-32 w-full" />
        </CardContent>
      </Card>
    );
  }

  if (!overview) {
    return (
      <Card role="alert">
        <CardHeader className="pb-4">
          <CardTitle>{t("Belohnung")}</CardTitle>
        </CardHeader>
        <CardContent>
          <Button type="button" size="sm" variant="outline" onClick={() => void reload()}>
            <RefreshCw aria-hidden="true" />
            {t("Erneut versuchen")}
          </Button>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-3 space-y-0 pb-3">
        <div>
          <CardTitle>{t("Belohnung")}</CardTitle>
        </div>
        <Button type="button" size="icon" variant="ghost" onClick={() => void reload()} aria-label={t("Belohnungen neu laden")} disabled={status === "loading"}>
          <RefreshCw className={cn(status === "loading" && "animate-spin")} aria-hidden="true" />
        </Button>
      </CardHeader>

      {/*
        Der Ladefehler wandert mit in den Inhalt, statt in einen eigenen
        `CardContent` darueber: zwei stapelweise Abschnitte mit eigenem Innenabstand
        waeren eine Haarlinie zu viel und wuerden die Karte beim Wechsel zwischen
        Laden und Anzeigen springen lassen.
      */}
      <KartenInhalt overview={overview} error={error} />
    </Card>
  );
}
