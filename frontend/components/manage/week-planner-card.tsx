"use client";

import { useEffect, useState, type ChangeEvent, type FormEvent } from "react";
import { CalendarCheck, LoaderCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Field, FormError } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { formatDate, formatDuration, formatTime } from "@/lib/format";
import {
  applyWeekPlan,
  DEFAULT_PLANNER_OPTIONS,
  isNewBlock,
  KIND_LABEL,
  loadOptions,
  previewWeekPlan,
  saveOptions,
  summarizePlan,
  validateOptions,
  type PlannerBlock,
  type PlannerBusy,
  type PlannerDay,
  type PlannerOptions,
  type PlannerResult,
} from "@/lib/planner";
import { cn } from "@/lib/utils";
import { t, tn } from "@/lib/i18n";

type Zahlenfeld = "max_minutes_per_day" | "break_minutes" | "after_school_minutes" | "homework_minutes" | "exam_topic_minutes" | "exam_lead_days";
type Uhrzeitfeld = "earliest" | "weekend_earliest" | "latest";

/**
 * Der Wochenplaner: Stundenplan + offene Aufgaben + Klausuren -> Lernplan.
 *
 * Zwei Schritte, wie beim Assistenten: erst der Vorschlag, dann "Übernehmen".
 * Der Vorschlag schreibt nichts. Die Übernahme rechnet auf dem Server mit
 * denselben Einstellungen neu und trägt genau das ein - neue Blöcke für
 * Hausaufgaben und Klausurthemen landen im Plan "Wochenplan".
 *
 * `onApplied` lässt die Seite Pläne und Aufgaben neu laden; die Karte selbst
 * kennt die anderen Karten nicht.
 */
export function WeekPlannerCard({ onApplied }: { onApplied?: () => void }) {
  const [optionen, setOptionen] = useState<PlannerOptions>(DEFAULT_PLANNER_OPTIONS);
  const [vorschlag, setVorschlag] = useState<PlannerResult | null>(null);
  const [laeuft, setLaeuft] = useState<"preview" | "apply" | null>(null);
  const [fehler, setFehler] = useState<string | null>(null);
  const [meldung, setMeldung] = useState<string | null>(null);
  const [mehr, setMehr] = useState(false);

  useEffect(() => {
    // Erst nach dem ersten Rendern: der Server kennt den Speicher des
    // Browsers nicht, und ein abweichender Startwert waere ein Hydrationsfehler.
    const timer = window.setTimeout(() => setOptionen(loadOptions()), 0);
    return () => window.clearTimeout(timer);
  }, []);

  function setze<K extends keyof PlannerOptions>(key: K, value: PlannerOptions[K]) {
    setOptionen((current) => ({ ...current, [key]: value }));
    // Ein alter Vorschlag passt nicht mehr zu neuen Einstellungen.
    setVorschlag(null);
    setMeldung(null);
  }

  async function berechnen(event?: FormEvent) {
    event?.preventDefault();
    const problem = validateOptions(optionen);
    if (problem) {
      setFehler(problem);
      return;
    }
    setLaeuft("preview");
    setFehler(null);
    setMeldung(null);
    try {
      saveOptions(optionen);
      setVorschlag(await previewWeekPlan(optionen));
    } catch (error) {
      setFehler(error instanceof Error ? error.message : t("Der Vorschlag konnte nicht berechnet werden."));
    } finally {
      setLaeuft(null);
    }
  }

  async function uebernehmen() {
    setLaeuft("apply");
    setFehler(null);
    try {
      const ergebnis = await applyWeekPlan(optionen);
      setVorschlag(null);
      setMeldung(
        ergebnis.planned_count === 0
          ? t("Es gab nichts mehr einzutragen.")
          : ergebnis.created_count > 0
            ? tn(ergebnis.planned_count, "{n} Block eingetragen, davon {neu} als neue Aufgabe.", "{n} Blöcke eingetragen, davon {neu} als neue Aufgabe.", { neu: ergebnis.created_count })
            : tn(ergebnis.planned_count, "{n} Block eingetragen.", "{n} Blöcke eingetragen."),
      );
      onApplied?.();
    } catch (error) {
      setFehler(error instanceof Error ? error.message : t("Der Plan konnte nicht übernommen werden."));
    } finally {
      setLaeuft(null);
    }
  }

  const zahl = (key: Zahlenfeld) => ({
    type: "number",
    inputMode: "numeric" as const,
    value: Number.isFinite(optionen[key]) ? String(optionen[key]) : "",
    onChange: (event: ChangeEvent<HTMLInputElement>) => setze(key, event.target.valueAsNumber),
    disabled: laeuft !== null,
  });
  const uhrzeit = (key: Uhrzeitfeld) => ({
    type: "time",
    step: 300,
    value: optionen[key],
    onChange: (event: ChangeEvent<HTMLInputElement>) => setze(key, event.target.value),
    disabled: laeuft !== null,
  });

  const zusammenfassung = vorschlag ? summarizePlan(vorschlag) : null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("Wochenplan")}</CardTitle>
        <CardDescription>
          {t("Verteilt offene Aufgaben, Hausaufgaben und Klausurthemen auf die freie Zeit neben Unterricht und Terminen. Erst kommt ein Vorschlag, eingetragen wird beim Übernehmen.")}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <form onSubmit={(event) => void berechnen(event)} className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-5">
            <Field id="plan-zeitraum" label={t("Zeitraum")}>
              {(props) => (
                <Select {...props} value={String(optionen.days)} onChange={(event) => setze("days", Number(event.target.value))} disabled={laeuft !== null}>
                  <option value="7">{t("7 Tage")}</option>
                  <option value="14">{t("14 Tage")}</option>
                </Select>
              )}
            </Field>
            <Field id="plan-ab" label={t("Lernen ab")}>
              {(props) => <Input {...props} {...uhrzeit("earliest")} />}
            </Field>
            <Field id="plan-wochenende-ab" label={t("Wochenende ab")}>
              {(props) => <Input {...props} {...uhrzeit("weekend_earliest")} />}
            </Field>
            <Field id="plan-bis" label={t("Spätestens bis")}>
              {(props) => <Input {...props} {...uhrzeit("latest")} />}
            </Field>
            <Field id="plan-budget" label={t("Minuten pro Tag")}>
              {(props) => <Input {...props} min={15} max={600} {...zahl("max_minutes_per_day")} />}
            </Field>
          </div>

          <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-[13px]">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={optionen.include_weekends}
                onChange={(event) => setze("include_weekends", event.target.checked)}
                disabled={laeuft !== null}
                className="h-4 w-4 border-input bg-card accent-[var(--foreground)]"
              />
              {t("Auch am Wochenende")}
            </label>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={optionen.replan_scheduled}
                onChange={(event) => setze("replan_scheduled", event.target.checked)}
                disabled={laeuft !== null}
                className="h-4 w-4 border-input bg-card accent-[var(--foreground)]"
              />
              {t("Schon geplante Aufgaben neu verteilen")}
            </label>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={optionen.include_reviews}
                onChange={(event) => setze("include_reviews", event.target.checked)}
                disabled={laeuft !== null}
                className="h-4 w-4 border-input bg-card accent-[var(--foreground)]"
              />
              {t("Fällige Wiederholungen einplanen")}
            </label>
            <Button type="button" variant="link" size="sm" onClick={() => setMehr((offen) => !offen)} aria-expanded={mehr}>
              {mehr ? t("Weniger Einstellungen") : t("Mehr Einstellungen")}
            </Button>
          </div>

          {mehr ? (
            <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-5">
              <Field id="plan-pause" label={t("Pause zwischen Blöcken")} hint={t("Minuten")}>
                {(props) => <Input {...props} min={0} max={60} {...zahl("break_minutes")} />}
              </Field>
              <Field id="plan-schulschluss" label={t("Pause nach der Schule")} hint={t("Minuten")}>
                {(props) => <Input {...props} min={0} max={240} {...zahl("after_school_minutes")} />}
              </Field>
              <Field id="plan-hausaufgabe" label={t("Je Hausaufgabe")} hint={t("Minuten")}>
                {(props) => <Input {...props} min={5} max={240} {...zahl("homework_minutes")} />}
              </Field>
              <Field id="plan-thema" label={t("Je Klausurthema")} hint={t("Minuten")}>
                {(props) => <Input {...props} min={5} max={240} {...zahl("exam_topic_minutes")} />}
              </Field>
              <Field id="plan-vorlauf" label={t("Klausur-Vorlauf")} hint={t("Tage vor der Klausur")}>
                {(props) => <Input {...props} min={1} max={60} {...zahl("exam_lead_days")} />}
              </Field>
            </div>
          ) : null}

          <div className="flex flex-wrap items-center gap-2">
            <Button type="submit" variant={vorschlag ? "outline" : "default"} disabled={laeuft !== null}>
              {laeuft === "preview" ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : null}
              {vorschlag ? t("Neu berechnen") : t("Vorschlag berechnen")}
            </Button>
            {zusammenfassung && zusammenfassung.blocks > 0 ? (
              <Button type="button" onClick={() => void uebernehmen()} disabled={laeuft !== null}>
                {laeuft === "apply" ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : null}
                {t("Übernehmen")}
              </Button>
            ) : null}
            {vorschlag ? (
              <Button type="button" variant="ghost" onClick={() => setVorschlag(null)} disabled={laeuft !== null}>
                {t("Verwerfen")}
              </Button>
            ) : null}
          </div>
          <FormError message={fehler} />
          {meldung ? (
            <p className="text-[13px] text-muted-foreground" role="status">
              {meldung}
            </p>
          ) : null}
        </form>

        {vorschlag && zusammenfassung ? (
          <div className="space-y-3 border-t border-border pt-4">
            {zusammenfassung.blocks === 0 && zusammenfassung.unplaced === 0 ? (
              <EmptyState
                icon={CalendarCheck}
                title={t("Nichts zu planen")}
                description={t("Es gibt keine offenen Aufgaben, Hausaufgaben oder anstehenden Klausuren ohne Termin.")}
              />
            ) : (
              <p className="text-[13px] text-muted-foreground">
                {tn(zusammenfassung.blocks, "{n} Block, zusammen {dauer}", "{n} Blöcke, zusammen {dauer}", { dauer: formatDuration(zusammenfassung.minutes) })}
                {zusammenfassung.newBlocks > 0 ? ` · ${t("{n} neu", { n: zusammenfassung.newBlocks })}` : ""}
                {zusammenfassung.late > 0 ? ` · ${t("{n} nach Frist", { n: zusammenfassung.late })}` : ""}
                {zusammenfassung.unplaced > 0 ? ` · ${t("{n} ohne Platz", { n: zusammenfassung.unplaced })}` : ""}
              </p>
            )}

            <ol className="divide-y divide-border rounded-md border border-border">
              {vorschlag.days.map((tag) => (
                <PlanTag key={tag.date} tag={tag} />
              ))}
            </ol>

            {vorschlag.unplaced.length > 0 ? (
              <div className="space-y-1.5">
                <h3 className="text-[13px] font-medium">{t("Ohne Platz")}</h3>
                <ul className="space-y-1 text-[13px]">
                  {vorschlag.unplaced.map((eintrag, index) => (
                    <li key={`${eintrag.title}-${index}`} className="flex flex-wrap items-baseline gap-x-2">
                      <span>{eintrag.title}</span>
                      <span className="text-muted-foreground">
                        {formatDuration(eintrag.duration_minutes)} · {eintrag.reason}
                      </span>
                    </li>
                  ))}
                </ul>
                <p className="text-[12px] text-muted-foreground">{t("Mehr Minuten pro Tag, ein späteres Ende oder 14 Tage schaffen Platz.")}</p>
              </div>
            ) : null}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function PlanTag({ tag }: { tag: PlannerDay }) {
  const unterricht = tag.busy.filter((eintrag) => eintrag.kind === "lesson");
  const sonstiges = tag.busy.filter((eintrag) => eintrag.kind !== "lesson");
  const schulschluss = unterricht.length > 0 ? unterricht[unterricht.length - 1].end : null;
  // Feste Termine und neue Bloecke in einer Zeitleiste, nicht in zwei Listen:
  // sonst steht "Training 18:00" ueber einem Block um 16:00.
  const zeilen: Array<{ art: "fest"; start: string; eintrag: PlannerBusy } | { art: "block"; start: string; block: PlannerBlock }> = [
    ...sonstiges.map((eintrag) => ({ art: "fest" as const, start: eintrag.start, eintrag })),
    ...tag.blocks.map((block) => ({ art: "block" as const, start: block.start, block })),
  ].sort((links, rechts) => Date.parse(links.start) - Date.parse(rechts.start));

  return (
    <li className="grid gap-2 px-3 py-2.5 sm:grid-cols-[9rem_1fr]">
      <div>
        <p className="text-[13px] font-medium">{formatDate(tag.date, { weekday: "short", day: "2-digit", month: "2-digit" })}</p>
        <p className="text-[12px] text-muted-foreground">
          {tag.budget_minutes === 0 ? t("Frei") : t("{geplant} / {budget} Min.", { geplant: tag.planned_minutes, budget: tag.budget_minutes })}
          {schulschluss ? ` · Schule bis ${formatTime(schulschluss)}` : ""}
        </p>
      </div>
      <div className="min-w-0 space-y-1">
        {zeilen.map((zeile, index) =>
          zeile.art === "fest" ? (
            <p key={`fest-${index}`} className="flex gap-2 text-[12px] text-muted-foreground">
              <span className="w-24 shrink-0 tabular-nums">
                {formatTime(zeile.eintrag.start)}–{formatTime(zeile.eintrag.end)}
              </span>
              <span className="truncate">
                {zeile.eintrag.title}
                {zeile.eintrag.kind === "task" ? t(" (schon geplant)") : ""}
              </span>
            </p>
          ) : (
            <div key={`block-${index}`} className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px]">
              <span className="w-24 shrink-0 tabular-nums text-muted-foreground">
                {formatTime(zeile.block.start)}–{formatTime(zeile.block.end)}
              </span>
              <span className={cn("min-w-0", zeile.block.late && "font-medium")}>{zeile.block.title}</span>
              {zeile.block.subject_name ? <span className="text-[12px] text-muted-foreground">{zeile.block.subject_name}</span> : null}
              {zeile.block.kind !== "task" ? <Badge variant="outline">{KIND_LABEL[zeile.block.kind]}</Badge> : null}
              {isNewBlock(zeile.block) ? <Badge variant="secondary">{t("Neu")}</Badge> : null}
              {zeile.block.late ? <Badge variant="warning">{t("Nach Frist")}</Badge> : null}
            </div>
          ),
        )}
        {zeilen.length === 0 ? <p className="text-[12px] text-muted-foreground">{t("Nichts geplant")}</p> : null}
      </div>
    </li>
  );
}
