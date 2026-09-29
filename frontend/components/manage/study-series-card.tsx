"use client";

import { useState, type FormEvent } from "react";
import { CheckCircle2, LoaderCircle, Plus, Repeat, RefreshCw, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Field, FormError } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { RowActions, SectionBanner } from "@/components/manage/row-actions";
import { cancelStudySeries, createStudySeries, getStudySeries, updateStudySeries } from "@/lib/api";
import { toDateInput, toUtcIsoFromParts } from "@/lib/datetime";
import { formatDate, formatDuration, toIsoDate } from "@/lib/format";
import { useCollection } from "@/lib/use-collection";
import { cn } from "@/lib/utils";
import { MAX_TASK_PRIORITY, type StudyPlan, type StudyTaskRecurrence, type StudyTaskSeries, type StudyTaskSeriesInput } from "@/lib/types";

const WIEDERHOLUNG_LABEL: Record<StudyTaskRecurrence, string> = {
  daily: "täglich",
  weekly: "wöchentlich",
  biweekly: "alle zwei Wochen",
  monthly: "monatlich",
};

const WIEDERHOLUNGEN = Object.entries(WIEDERHOLUNG_LABEL) as Array<[StudyTaskRecurrence, string]>;

interface SerienFormular {
  titel: string;
  beschreibung: string;
  plan: string;
  wiederholung: StudyTaskRecurrence;
  dauer: string;
  prioritaet: string;
  start: string;
  bis: string;
  grenze: string;
}

const leeresFormular = (): SerienFormular => ({
  titel: "",
  beschreibung: "",
  plan: "",
  wiederholung: "weekly",
  dauer: "20",
  prioritaet: "2",
  start: toIsoDate(new Date()),
  bis: "",
  grenze: "",
});

/**
 * Serien und ihre Vorlage.
 *
 * `cancelStudySeries` ist ein `DELETE`, beendet aber die Serie und storniert
 * nur ihre **offenen** Aufgaben - erledigte bleiben stehen. Der Knopf heisst
 * darum "Serie beenden" und nicht "Serie löschen", weil er etwas voellig
 * Anderes tut als das, was der Pfeil erwarten laesst.
 */
export function StudySeriesCard({ plans, onChanged, refreshKey = 0 }: { plans: StudyPlan[]; onChanged?: () => void; refreshKey?: number }) {
  const sammlung = useCollection<StudyTaskSeries, StudyTaskSeriesInput, Partial<StudyTaskSeriesInput>>(
    () => getStudySeries(),
    {
      create: createStudySeries,
      update: updateStudySeries,
      remove: cancelStudySeries,
    },
    { sort: (a, b) => (a.next_due_on ?? "9999").localeCompare(b.next_due_on ?? "9999") || a.id - b.id, refreshKey },
  );
  const [formularOffen, setFormularOffen] = useState(false);
  const [bearbeitet, setBearbeitet] = useState<number | null>(null);
  const [formular, setFormular] = useState<SerienFormular>(leeresFormular());
  const [formularFehler, setFormularFehler] = useState<string | null>(null);

  const istSpeichern = sammlung.pending === "create" || sammlung.pending === `update:${bearbeitet}`;

  function oeffnen(serie?: StudyTaskSeries) {
    setBearbeitet(serie?.id ?? null);
    setFormular(
      serie
        ? {
            titel: serie.title,
            beschreibung: serie.description ?? "",
            plan: String(serie.study_plan_id),
            wiederholung: serie.recurrence,
            dauer: String(serie.duration_minutes),
            prioritaet: String(serie.priority),
            // Kein `start`: `starts_on` ist der Beginn der Reihe und nicht
            // der naechste Termin. Die Bearbeitung aendert nur die Vorlage
            // fuer kuenftige Aufgaben, deshalb wird das Feld im
            // Bearbeitungsformular gar nicht angeboten. Ein leerer Wert
            // genuegt dem Typ, weil `absenden` es nur beim Anlegen liest.
            start: "",
            bis: toDateInput(serie.until),
            grenze: serie.occurrence_limit === null ? "" : String(serie.occurrence_limit),
          }
        : { ...leeresFormular(), plan: plans[0] ? String(plans[0].id) : "" },
    );
    setFormularFehler(null);
    setFormularOffen(true);
  }

  function schliessen() {
    setFormularOffen(false);
    setBearbeitet(null);
    setFormular(leeresFormular());
    setFormularFehler(null);
  }

  async function absenden(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!formular.titel.trim()) {
      setFormularFehler("Gib der Serie einen Titel.");
      return;
    }
    const dauer = Number(formular.dauer);
    if (!Number.isInteger(dauer) || dauer < 1 || dauer > 1440) {
      setFormularFehler("Die Dauer muss zwischen 1 und 1440 Minuten liegen.");
      return;
    }
    const prioritaet = Number(formular.prioritaet);
    if (!Number.isInteger(prioritaet) || prioritaet < 0 || prioritaet > MAX_TASK_PRIORITY) {
      setFormularFehler(`Die Priorität muss zwischen 0 und ${MAX_TASK_PRIORITY} liegen.`);
      return;
    }

    const grenze = formular.grenze.trim() ? Number(formular.grenze) : null;
    if (grenze !== null && (!Number.isInteger(grenze) || grenze < 1 || grenze > 1000)) {
      setFormularFehler("Die Anzahl der Wiederholungen muss zwischen 1 und 1000 liegen.");
      return;
    }

    const bis = formular.bis.trim() ? toUtcIsoFromParts(formular.bis, "23:59") : null;
    if (formular.bis.trim() && !bis) {
      setFormularFehler("Dieses Datum lässt sich nicht lesen. Bitte erneut wählen.");
      return;
    }

    setFormularFehler(null);

    if (bearbeitet !== null) {
      // Nur die Vorlage aendern. `starts_on` und `study_plan_id` gehoeren
      // nicht dazu - eine bestehende Serie umzuhängen waere ein Verschieben
      // ihrer Vergangenheit.
      const ergebnis = await sammlung.update(bearbeitet, {
        title: formular.titel.trim(),
        description: formular.beschreibung.trim() || null,
        duration_minutes: dauer,
        priority: prioritaet,
        until: bis,
        occurrence_limit: grenze,
      });
      if (ergebnis) {
        schliessen();
        onChanged?.();
      }
      return;
    }

    const start = toUtcIsoFromParts(formular.start, "00:00");
    if (!start) {
      setFormularFehler("Ab wann soll die Serie laufen? Ein Startdatum wird benötigt.");
      return;
    }
    if (!formular.plan) {
      setFormularFehler("Wähle den Lernplan, zu dem die Serie gehört.");
      return;
    }

    const ergebnis = await sammlung.create({
      study_plan_id: Number(formular.plan),
      title: formular.titel.trim(),
      recurrence: formular.wiederholung,
      starts_on: start,
      duration_minutes: dauer,
      priority: prioritaet,
      description: formular.beschreibung.trim() || null,
      until: bis,
      occurrence_limit: grenze,
    });
    if (ergebnis) {
      schliessen();
      onChanged?.();
    }
  }

  return (
    <Card className="border-border/80 bg-card/90">
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-3 space-y-0 pb-4">
        <div>
          <CardTitle className="flex items-center gap-2">
            <Repeat className="h-5 w-5 text-primary" aria-hidden="true" />
            Wiederkehrende Serien
          </CardTitle>
          <CardDescription className="mt-1">Aus einer Vorlage entstehen automatisch einzelne Aufgaben.</CardDescription>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant="secondary">{sammlung.items.length} Serien</Badge>
          <Button type="button" size="icon" variant="ghost" onClick={() => void sammlung.reload()} aria-label="Serien neu laden" disabled={sammlung.status === "loading"}>
            <RefreshCw className={cn(sammlung.status === "loading" && "animate-spin")} aria-hidden="true" />
          </Button>
          <Button type="button" size="sm" onClick={() => (formularOffen && bearbeitet === null ? schliessen() : oeffnen())} aria-expanded={formularOffen && bearbeitet === null} disabled={plans.length === 0}>
            {formularOffen && bearbeitet === null ? <X aria-hidden="true" /> : <Plus aria-hidden="true" />}
            {formularOffen && bearbeitet === null ? "Schließen" : "Neu"}
          </Button>
        </div>
      </CardHeader>

      <CardContent className="space-y-4">
        <SectionBanner error={sammlung.error} actionError={sammlung.actionError} onRetry={() => void sammlung.reload()} />

        {plans.length === 0 ? (
          <p className="border border-dashed border-border bg-muted/30 px-4 py-3 text-xs leading-5 text-muted-foreground">
            Eine Serie gehört zu einem Lernplan. Lege oben zuerst einen Plan an.
          </p>
        ) : null}

        {formularOffen && plans.length > 0 ? (
          <form onSubmit={absenden} noValidate className="space-y-4 rounded-2xl border border-primary/20 bg-primary/5 p-4">
            <div className="flex items-start justify-between gap-3">
              <p className="text-sm font-bold">{bearbeitet === null ? "Neue Serie" : "Serie bearbeiten"}</p>
              <Button type="button" size="icon" variant="ghost" onClick={schliessen} disabled={istSpeichern} aria-label="Formular schließen">
                <X aria-hidden="true" />
              </Button>
            </div>

            <Field id="serie-titel" label="Titel" required>
              {(props) => (
                <Input
                  {...props}
                  value={formular.titel}
                  onChange={(event) => setFormular((current) => ({ ...current, titel: event.target.value }))}
                  disabled={istSpeichern}
                  placeholder="Zum Beispiel: Vokabeln zehn Minuten täglich"
                />
              )}
            </Field>

            <div className="grid gap-4 sm:grid-cols-2">
              {bearbeitet === null ? (
                <Field id="serie-plan" label="Lernplan" required>
                  {(props) => (
                    <Select
                      {...props}
                      value={formular.plan}
                      onChange={(event) => setFormular((current) => ({ ...current, plan: event.target.value }))}
                      disabled={istSpeichern}
                    >
                      <option value="">Bitte wählen</option>
                      {plans.map((plan) => (
                        <option key={plan.id} value={plan.id}>
                          {plan.title}
                        </option>
                      ))}
                    </Select>
                  )}
                </Field>
              ) : null}

              <Field id="serie-wiederholung" label="Wiederholung" required hint={bearbeitet !== null ? "Gilt erst ab der nächsten Aufgabe." : undefined}>
                {(props) => (
                  <Select
                    {...props}
                    value={formular.wiederholung}
                    onChange={(event) => setFormular((current) => ({ ...current, wiederholung: event.target.value as StudyTaskRecurrence }))}
                    disabled={istSpeichern || bearbeitet !== null}
                  >
                    {WIEDERHOLUNGEN.map(([wert, label]) => (
                      <option key={wert} value={wert}>
                        {label}
                      </option>
                    ))}
                  </Select>
                )}
              </Field>
            </div>

            <div className="grid gap-4 sm:grid-cols-3">
              <Field id="serie-dauer" label="Dauer in Minuten" required>
                {(props) => (
                  <Input
                    {...props}
                    type="number"
                    min={1}
                    max={1440}
                    step={5}
                    value={formular.dauer}
                    onChange={(event) => setFormular((current) => ({ ...current, dauer: event.target.value }))}
                    disabled={istSpeichern}
                  />
                )}
              </Field>

              <Field id="serie-prioritaet" label={`Priorität (0–${MAX_TASK_PRIORITY})`} required>
                {(props) => (
                  <Input
                    {...props}
                    type="number"
                    min={0}
                    max={MAX_TASK_PRIORITY}
                    value={formular.prioritaet}
                    onChange={(event) => setFormular((current) => ({ ...current, prioritaet: event.target.value }))}
                    disabled={istSpeichern}
                  />
                )}
              </Field>

              {bearbeitet === null ? (
                <Field id="serie-start" label="Start" required>
                  {(props) => (
                    <Input
                      {...props}
                      type="date"
                      value={formular.start}
                      onChange={(event) => setFormular((current) => ({ ...current, start: event.target.value }))}
                      disabled={istSpeichern}
                    />
                  )}
                </Field>
              ) : null}
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <Field id="serie-bis" label="Läuft bis" hint="Optional. Danach endet die Serie.">
                {(props) => (
                  <Input
                    {...props}
                    type="date"
                    value={formular.bis}
                    onChange={(event) => setFormular((current) => ({ ...current, bis: event.target.value }))}
                    disabled={istSpeichern}
                  />
                )}
              </Field>

              <Field id="serie-grenze" label="Höchstzahl" hint="Optional. Anzahl der erzeugten Aufgaben, höchstens 1000.">
                {(props) => (
                  <Input
                    {...props}
                    type="number"
                    min={1}
                    max={1000}
                    value={formular.grenze}
                    onChange={(event) => setFormular((current) => ({ ...current, grenze: event.target.value }))}
                    disabled={istSpeichern}
                    placeholder="unbegrenzt"
                  />
                )}
              </Field>
            </div>

            <Field id="serie-beschreibung" label="Notiz" hint="Optional.">
              {(props) => (
                <Textarea
                  {...props}
                  value={formular.beschreibung}
                  onChange={(event) => setFormular((current) => ({ ...current, beschreibung: event.target.value }))}
                  disabled={istSpeichern}
                />
              )}
            </Field>

            <FormError message={formularFehler} />

            <div className="flex flex-wrap items-center gap-2">
              <Button type="submit" disabled={istSpeichern}>
                {istSpeichern ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <CheckCircle2 aria-hidden="true" />}
                {istSpeichern ? "Wird gespeichert …" : bearbeitet === null ? "Serie anlegen" : "Vorlage speichern"}
              </Button>
              <Button type="button" variant="ghost" onClick={schliessen} disabled={istSpeichern}>
                Abbrechen
              </Button>
            </div>
          </form>
        ) : null}

        {sammlung.status === "loading" && sammlung.items.length === 0 ? (
          <div className="space-y-3" aria-busy="true" aria-label="Serien werden geladen">
            <Skeleton className="h-16 w-full" />
          </div>
        ) : sammlung.items.length === 0 ? (
          <EmptyState icon={Repeat} title="Keine Serien" description="Eine Serie erzeugt aus einer Vorlage mehrere Aufgaben – etwa tägliche Vokabelübungen." />
        ) : (
          <ul className="space-y-2.5">
            {sammlung.items.map((serie) => (
              <li key={serie.id} className="flex items-start gap-3 border border-border bg-card/60 p-3.5 transition-colors hover:border-foreground/40">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className={cn("truncate text-sm font-extrabold", serie.status !== "active" && "text-muted-foreground")}>{serie.title}</p>
                    <Badge variant="outline">{WIEDERHOLUNG_LABEL[serie.recurrence]}</Badge>
                    {serie.status !== "active" ? <Badge variant="secondary">{serie.status === "finished" ? "Beendet" : "Storniert"}</Badge> : null}
                  </div>
                  {serie.description ? <p className="mt-1 line-clamp-2 text-xs leading-5 text-muted-foreground">{serie.description}</p> : null}
                  <p className="mt-1.5 text-xs font-semibold text-muted-foreground">
                    {formatDuration(serie.duration_minutes)}
                    <span className="text-border"> · </span>
                    Priorität {serie.priority}
                    <span className="text-border"> · </span>
                    {serie.occurrences} {serie.occurrences === 1 ? "Aufgabe" : "Aufgaben"}
                    {serie.next_due_on ? (
                      <>
                        <span className="text-border"> · </span>
                        als Nächstes {formatDate(serie.next_due_on, { day: "2-digit", month: "short" })}
                      </>
                    ) : null}
                    {serie.until ? (
                      <>
                        <span className="text-border"> · </span>
                        bis {formatDate(serie.until, { day: "2-digit", month: "short", year: "numeric" })}
                      </>
                    ) : null}
                  </p>
                </div>

                {serie.status === "active" ? (
                  <RowActions
                    label={serie.title}
                    onEdit={() => oeffnen(serie)}
                    onDelete={async () => {
                      const beendet = await sammlung.remove(serie.id);
                      if (beendet) {
                        onChanged?.();
                      }
                    }}
                    busy={sammlung.pending !== null}
                  />
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

