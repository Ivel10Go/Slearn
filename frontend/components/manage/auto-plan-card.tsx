"use client";

import { useState } from "react";
import { CalendarCheck2, LoaderCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { KIND_LABEL, generateAutoPlan, groupByDay, type AutoPlan } from "@/lib/auto-plan";
import { formatDate, formatDuration, formatTime } from "@/lib/format";

const TAGE = [7, 14, 21] as const;
const MINUTEN = [30, 45, 60, 90, 120, 150] as const;

/**
 * Lernplan aus Stundenplan, Hausaufgaben, Klausuren und Wiederholungen.
 *
 * Zwei Schritte: erst ein Vorschlag, der nichts schreibt, dann "Übernehmen".
 * Ein Plan, der ungefragt zwanzig Aufgaben anlegt, wäre schwer wieder
 * loszuwerden. Ein zweiter Lauf legt nur an, was neu dazugekommen ist.
 */
export function AutoPlanCard({ onApplied }: { onApplied: () => void }) {
  const [tage, setTage] = useState<number>(14);
  const [minuten, setMinuten] = useState<number>(90);
  const [vorschlag, setVorschlag] = useState<AutoPlan | null>(null);
  const [laedt, setLaedt] = useState<"vorschlag" | "anwenden" | null>(null);
  const [fehler, setFehler] = useState<string | null>(null);
  const [meldung, setMeldung] = useState<string | null>(null);

  async function berechnen(anwenden: boolean) {
    setLaedt(anwenden ? "anwenden" : "vorschlag");
    setFehler(null);
    setMeldung(null);
    try {
      const ergebnis = await generateAutoPlan({ days: tage, dailyMinutes: minuten, apply: anwenden });
      if (anwenden) {
        setVorschlag(null);
        setMeldung(
          ergebnis.applied
            ? `${ergebnis.items.length} Lerneinheiten eingeplant.`
            : "Es gab nichts Neues einzuplanen.",
        );
        onApplied();
      } else {
        setVorschlag(ergebnis);
      }
    } catch (error) {
      setFehler(error instanceof Error ? error.message : "Der Lernplan konnte nicht berechnet werden.");
    } finally {
      setLaedt(null);
    }
  }

  const gruppen = vorschlag ? groupByDay(vorschlag) : [];
  const ueberbucht = vorschlag?.days.filter((tag) => tag.overbooked).length ?? 0;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Lernplan erstellen</CardTitle>
        <CardDescription>
          Verteilt Hausaufgaben, Klausurthemen (das unsicherste zuerst) und fällige Wiederholungen auf die freie Zeit nach dem
          Unterricht.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
          <label className="space-y-1 text-[13px]">
            <span className="block text-muted-foreground">Zeitraum</span>
            <Select value={tage} onChange={(event) => setTage(Number(event.target.value))}>
              {TAGE.map((wert) => (
                <option key={wert} value={wert}>
                  {wert === 7 ? "1 Woche" : `${wert / 7} Wochen`}
                </option>
              ))}
            </Select>
          </label>
          <label className="space-y-1 text-[13px]">
            <span className="block text-muted-foreground">Lernzeit pro Tag</span>
            <Select value={minuten} onChange={(event) => setMinuten(Number(event.target.value))}>
              {MINUTEN.map((wert) => (
                <option key={wert} value={wert}>
                  {formatDuration(wert)}
                </option>
              ))}
            </Select>
          </label>
          <Button type="button" variant="outline" onClick={() => void berechnen(false)} disabled={laedt !== null}>
            {laedt === "vorschlag" ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : null}
            Vorschlag
          </Button>
        </div>

        {fehler ? (
          <p className="text-[13px] text-destructive" role="alert">
            {fehler}
          </p>
        ) : null}
        {meldung ? <p className="text-[13px] text-muted-foreground">{meldung}</p> : null}

        {vorschlag ? (
          vorschlag.items.length === 0 ? (
            <p className="text-[13px] text-muted-foreground">
              Nichts Neues einzuplanen
              {vorschlag.skipped > 0 ? ` – ${vorschlag.skipped} Einträge sind schon im Plan.` : "."}
            </p>
          ) : (
            <div className="space-y-3">
              {ueberbucht > 0 ? (
                <p className="rounded-md border border-foreground px-3 py-2 text-[13px]" role="status">
                  An {ueberbucht === 1 ? "einem Tag" : `${ueberbucht} Tagen`} reicht die Lernzeit nicht. Mehr Zeit pro Tag
                  wählen oder einzelne Einheiten später verschieben.
                </p>
              ) : null}
              <ol className="space-y-3">
                {gruppen.map((gruppe) => (
                  <li key={gruppe.day.day} className="space-y-1">
                    <div className="flex items-baseline justify-between gap-2 text-[13px]">
                      <span className="font-medium">{formatDate(gruppe.day.starts_at, { weekday: "long", day: "2-digit", month: "2-digit" })}</span>
                      <span className="text-xs text-muted-foreground">
                        {formatDuration(gruppe.day.planned_minutes)}
                        {gruppe.day.overbooked ? " · überbucht" : ""}
                      </span>
                    </div>
                    <ul className="divide-y divide-border rounded-md border border-border">
                      {gruppe.items.map((item, index) => (
                        <li key={`${item.title}-${index}`} className="flex items-center gap-3 px-3 py-2 text-[13px]">
                          <span className="w-11 shrink-0 tabular-nums text-muted-foreground">{formatTime(item.starts_at)}</span>
                          <span className="min-w-0 flex-1 truncate">{item.title}</span>
                          <Badge variant="outline">{KIND_LABEL[item.kind]}</Badge>
                          <span className="shrink-0 text-xs text-muted-foreground">{formatDuration(item.duration_minutes)}</span>
                        </li>
                      ))}
                    </ul>
                  </li>
                ))}
              </ol>
              <div className="flex items-center justify-between gap-3">
                <span className="text-xs text-muted-foreground">
                  {vorschlag.items.length} Einheiten{vorschlag.skipped > 0 ? `, ${vorschlag.skipped} schon geplant` : ""}
                </span>
                <Button type="button" onClick={() => void berechnen(true)} disabled={laedt !== null}>
                  {laedt === "anwenden" ? (
                    <LoaderCircle className="animate-spin" aria-hidden="true" />
                  ) : (
                    <CalendarCheck2 aria-hidden="true" />
                  )}
                  Übernehmen
                </Button>
              </div>
            </div>
          )
        ) : null}
      </CardContent>
    </Card>
  );
}
