"use client";

import { useState, type FormEvent } from "react";
import { CheckCircle2, ListPlus, LoaderCircle, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Field, FormError } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { RowActions, SectionBanner, SectionHeader } from "@/components/manage/row-actions";
import { createExam, deleteExam, generateExamTasks, getExams, getStudyPlans, updateExam, type ExamChanges, type ExamWrite } from "@/lib/api";
import { toDateTimeLocal, toUtcIso } from "@/lib/datetime";
import { formatDate, getCountdown } from "@/lib/format";
import { errorMessage, useCollection, useList } from "@/lib/use-collection";
import { useSubjectOptions } from "@/lib/use-subject-options";
import { cn } from "@/lib/utils";
import type { Exam, ExamTaskPlanResult, StudyPlan } from "@/lib/types";

const anchorClass = "scroll-mt-24";

/** Wie weit der erzeugte Plan zurueckreicht, wenn die Lernende nichts angibt. */
const STANDARD_VORLAUF = 14;

interface PruefungenFormular {
  titel: string;
  beschreibung: string;
  datum: string;
  fach: string;
}

const leeresFormular: PruefungenFormular = { titel: "", beschreibung: "", datum: "", fach: "" };

function ausPruefung(pruefung: Exam): PruefungenFormular {
  return {
    titel: pruefung.title,
    beschreibung: pruefung.description ?? "",
    datum: toDateTimeLocal(pruefung.date),
    fach: pruefung.subject_id === null ? "" : String(pruefung.subject_id),
  };
}

function alsEingabe(formular: PruefungenFormular): ExamWrite {
  const datum = toUtcIso(formular.datum);
  return {
    title: formular.titel.trim(),
    date: datum ?? formular.datum,
    description: formular.beschreibung.trim() || null,
    subject_id: formular.fach ? Number(formular.fach) : null,
  };
}

function alsAenderung(formular: PruefungenFormular): ExamChanges {
  const datum = toUtcIso(formular.datum);
  return {
    title: formular.titel.trim(),
    date: datum ?? formular.datum,
    description: formular.beschreibung.trim() || null,
    subject_id: formular.fach ? Number(formular.fach) : null,
  };
}

export function ExamsSection({ reference }: { reference: Date }) {
  const fach = useSubjectOptions();
  const sammlung = useCollection<Exam, ExamWrite, ExamChanges>(
    // `upcoming=false`: eine abgelaufene Klausur zu loeschen ist eine ganz
    // normale Aufgabe, und der Eintrag muss da sein, wenn man danach sucht.
    () => getExams(false),
    { create: createExam, update: updateExam, remove: deleteExam },
    { sort: (a, b) => a.date.localeCompare(b.date) || a.id - b.id },
  );
  const [formularOffen, setFormularOffen] = useState(false);
  const [bearbeitet, setBearbeitet] = useState<number | null>(null);
  const [formular, setFormular] = useState<PruefungenFormular>(leeresFormular);
  const [formularFehler, setFormularFehler] = useState<string | null>(null);
  const [vergangenOffen, setVergangenOffen] = useState(false);
  // Der Generator gehoert zu den Pruefungen, weil die Themen dort stehen.
  // Ohne Plan gibt es nichts, wohin erzeugt werden koennte, also laedt diese
  // Kachel die Plaene selbst - nur lesend, angelegt werden sie unter /lernen.
  const plaene = useList<StudyPlan>(() => getStudyPlans());
  const [zielPruefung, setZielPruefung] = useState<number | null>(null);
  const [zielPlan, setZielPlan] = useState<string>("");
  const [vorlauf, setVorlauf] = useState(String(STANDARD_VORLAUF));
  const [laueftAuf, setLaeuftAuf] = useState<number | null>(null);
  const [planFehler, setPlanFehler] = useState<string | null>(null);
  const [planErgebnis, setPlanErgebnis] = useState<ExamTaskPlanResult | null>(null);

  const istSpeichern = sammlung.pending === "create" || sammlung.pending === `update:${bearbeitet}`;

  /**
   * Erzeugt aus den Klausurthemen einen Lernplan.
   *
   * Ein zweiter Klick ist kein Duplikat: das Backend ueberspringt Themen, fuer
   * die im selben Plan schon eine offene Aufgabe an dieser Klausur haengt.
   * Deshalb wird hier auch kein Dialog gebraucht, sondern nur eine kurze
   * Rueckmeldung, wie viele Aufgaben neu entstanden sind.
   */
  async function aufgabenErzeugen() {
    if (zielPruefung === null) {
      return;
    }
    const planId = Number(zielPlan);
    if (!Number.isInteger(planId) || planId <= 0) {
      setPlanFehler("Wähle den Lernplan aus, in den die Aufgaben gehören.");
      return;
    }
    const tage = Number(vorlauf);
    if (!Number.isInteger(tage) || tage < 1 || tage > 180) {
      setPlanFehler("Der Vorlauf muss zwischen 1 und 180 Tagen liegen.");
      return;
    }

    setPlanFehler(null);
    setPlanErgebnis(null);
    setLaeuftAuf(zielPruefung);
    try {
      const ergebnis = await generateExamTasks(zielPruefung, { study_plan_id: planId, lead_days: tage });
      setPlanErgebnis(ergebnis);
      setZielPruefung(null);
    } catch (fehler) {
      setPlanFehler(errorMessage(fehler, "Die Aufgaben konnten nicht erzeugt werden."));
    } finally {
      setLaeuftAuf(null);
    }
  }

  function oeffnen(pruefung?: Exam) {
    setBearbeitet(pruefung?.id ?? null);
    setFormular(pruefung ? ausPruefung(pruefung) : leeresFormular);
    setFormularFehler(null);
    setFormularOffen(true);
  }

  function schliessen() {
    setFormularOffen(false);
    setBearbeitet(null);
    setFormular(leeresFormular);
    setFormularFehler(null);
  }

  async function absenden(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!formular.titel.trim()) {
      setFormularFehler("Gib der Prüfung einen Titel.");
      return;
    }
    if (!toUtcIso(formular.datum)) {
      setFormularFehler("Wann findet die Prüfung statt? Ein Datum wird benötigt.");
      return;
    }
    setFormularFehler(null);
    const ergebnis = bearbeitet === null ? await sammlung.create(alsEingabe(formular)) : await sammlung.update(bearbeitet, alsAenderung(formular));
    if (ergebnis) {
      schliessen();
    }
  }

  const vergangen = sammlung.items.filter((pruefung) => new Date(pruefung.date).getTime() < reference.getTime());
  const kommend = sammlung.items.filter((pruefung) => new Date(pruefung.date).getTime() >= reference.getTime());

  /**
   * Warum der Generator fuer diese Pruefung nicht geht - oder `null`.
   *
   * Frueher stand der Grund dauerhaft unter jeder Pruefung und zusaetzlich
   * als gestrichelter Kasten ueber der Liste. Jetzt erscheint er erst, wenn
   * man den Knopf drueckt: ein deaktivierter Knopf zeigt keinen Tooltip, und
   * ein Grund, den niemand gefragt hat, ist nur Rauschen.
   */
  function generatorGrund(pruefung: Exam) {
    if (pruefung.subject_id === null) {
      return "Diese Prüfung hat kein Fach – ohne Themen gibt es nichts zu erzeugen. Trag über den Stift ein Fach ein.";
    }
    if (plaene.items.length === 0) {
      return "Für den Generator braucht es einen Lernplan. Leg ihn unter „Lernplan“ an.";
    }
    return null;
  }

  function generatorUmschalten(pruefung: Exam) {
    setPlanErgebnis(null);
    const grund = generatorGrund(pruefung);
    if (grund) {
      setZielPruefung(null);
      setPlanFehler(grund);
      return;
    }
    setPlanFehler(null);
    setZielPruefung((aktuell) => (aktuell === pruefung.id ? null : pruefung.id));
    setZielPlan("");
  }

  function zeile(pruefung: Exam) {
    return (
      <PruefungsZeile
        key={pruefung.id}
        pruefung={pruefung}
        fachname={pruefung.subject_name}
        reference={reference}
        busy={sammlung.pending !== null}
        generatorOffen={zielPruefung === pruefung.id}
        generatorMoeglich={generatorGrund(pruefung) === null}
        laueft={laueftAuf === pruefung.id}
        onGenerator={() => generatorUmschalten(pruefung)}
        onEdit={() => oeffnen(pruefung)}
        onDelete={() => sammlung.remove(pruefung.id)}
      />
    );
  }

  return (
    <Card id="exams" className={cn(anchorClass, "@container")}>
      <SectionHeader
        title="Prüfungen"
        count={`${kommend.length} anstehend`}
        loading={sammlung.status === "loading"}
        onReload={() => void sammlung.reload()}
        formOpen={formularOffen && bearbeitet === null}
        onToggleForm={() => (formularOffen && bearbeitet === null ? schliessen() : oeffnen())}
        newLabel="Neue Prüfung"
      />

      <CardContent className="space-y-3 pb-0">
        <SectionBanner error={sammlung.error} actionError={sammlung.actionError} onRetry={() => void sammlung.reload()} />

        {/*
          Aus den Klausurthemen einen Lernplan bauen. Bisher verlinkte das
          Dashboard die Themen nur; jetzt entsteht daraus eine Aufgabe je Thema.
        */}
        {planErgebnis ? (
          <p className="rounded-md border border-border bg-muted/40 px-3 py-2 text-[13px]" role="status">
            {planErgebnis.created_count} Aufgaben angelegt
            {planErgebnis.skipped_count > 0 ? `, ${planErgebnis.skipped_count} Thema übersprungen` : ""}
            {planErgebnis.last_due_on ? ` · bis ${formatDate(planErgebnis.last_due_on)}` : ""}. Sie stehen unter{" "}
            <span className="font-medium">Heute lernen</span>.
          </p>
        ) : null}
        <FormError message={planFehler} />

        {zielPruefung !== null ? (
          <div className="space-y-4 rounded-md border border-border bg-muted/40 p-4">
            <p className="text-sm font-semibold">Lernplan aus den Themen erzeugen</p>
            <div className="grid gap-4 @md:grid-cols-2">
              <Field id="generator-plan" label="Lernplan" required>
                {(props) => (
                  <Select {...props} value={zielPlan} onChange={(event) => setZielPlan(event.target.value)} disabled={laueftAuf !== null}>
                    <option value="">Lernplan wählen …</option>
                    {plaene.items.map((plan) => (
                      <option key={plan.id} value={plan.id}>
                        {plan.title}
                      </option>
                    ))}
                  </Select>
                )}
              </Field>
              <Field id="generator-vorlauf" label="Vorlauf in Tagen" hint="Wie weit der Plan vor der Klausur zurückreicht.">
                {(props) => (
                  <Input
                    {...props}
                    type="number"
                    min={1}
                    max={180}
                    value={vorlauf}
                    onChange={(event) => setVorlauf(event.target.value)}
                    disabled={laueftAuf !== null}
                  />
                )}
              </Field>
            </div>
            <p className="text-xs leading-5 text-muted-foreground">
              Eine Aufgabe je Thema, verteilt über die Tage vor der Klausur – das schwächste Thema zuerst, weil es am
              meisten Vorlauf braucht. Die Aufgaben werden in dieser Reihenfolge verkettet, damit der Plan eine Reihenfolge
              hat. Ein zweiter Klick legt nichts doppelt an.
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <Button type="button" onClick={() => void aufgabenErzeugen()} disabled={laueftAuf !== null || !zielPlan}>
                {laueftAuf !== null ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <ListPlus aria-hidden="true" />}
                {laueftAuf !== null ? "Wird erzeugt …" : "Aufgaben erzeugen"}
              </Button>
              <Button type="button" variant="ghost" onClick={() => setZielPruefung(null)} disabled={laueftAuf !== null}>
                Abbrechen
              </Button>
            </div>
          </div>
        ) : null}

        {formularOffen ? (
          <form onSubmit={absenden} noValidate className="space-y-4 rounded-md border border-border bg-muted/40 p-4">
            <div className="flex items-start justify-between gap-3">
              <p className="text-sm font-semibold">{bearbeitet === null ? "Neue Prüfung" : "Prüfung bearbeiten"}</p>
              <Button type="button" size="icon" variant="ghost" onClick={schliessen} disabled={istSpeichern} aria-label="Formular schließen">
                <X aria-hidden="true" />
              </Button>
            </div>

            <Field id="pruefung-titel" label="Titel" required>
              {(props) => (
                <Input
                  {...props}
                  value={formular.titel}
                  onChange={(event) => setFormular((current) => ({ ...current, titel: event.target.value }))}
                  disabled={istSpeichern}
                  placeholder="Zum Beispiel: Klassenarbeit Analysis"
                />
              )}
            </Field>

            <div className="grid gap-4 @md:grid-cols-2">
              <Field id="pruefung-datum" label="Datum und Uhrzeit" required>
                {(props) => (
                  <Input
                    {...props}
                    type="datetime-local"
                    value={formular.datum}
                    onChange={(event) => setFormular((current) => ({ ...current, datum: event.target.value }))}
                    disabled={istSpeichern}
                  />
                )}
              </Field>

              <Field id="pruefung-fach" label="Fach" hint={fach.status === "loading" ? "Fächer werden geladen …" : undefined}>
                {(props) => (
                  <Select
                    {...props}
                    value={formular.fach}
                    onChange={(event) => setFormular((current) => ({ ...current, fach: event.target.value }))}
                    disabled={istSpeichern}
                  >
                    <option value="">Ohne Fach</option>
                    {fach.options.map((option) => (
                      <option key={option.id} value={option.id}>
                        {option.name}
                      </option>
                    ))}
                  </Select>
                )}
              </Field>
            </div>

            <Field id="pruefung-beschreibung" label="Hinweise" hint="Themen, Stoff, Ort – alles, was du wissen musst.">
              {(props) => (
                <Textarea
                  {...props}
                  value={formular.beschreibung}
                  onChange={(event) => setFormular((current) => ({ ...current, beschreibung: event.target.value }))}
                  disabled={istSpeichern}
                  placeholder="Kapitel 1 bis 3, Ableitungen und Integrale"
                />
              )}
            </Field>

            <FormError message={formularFehler} />

            <div className="flex flex-wrap items-center gap-2">
              <Button type="submit" disabled={istSpeichern}>
                {istSpeichern ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <CheckCircle2 aria-hidden="true" />}
                {istSpeichern ? "Wird gespeichert …" : bearbeitet === null ? "Prüfung anlegen" : "Änderungen speichern"}
              </Button>
              <Button type="button" variant="ghost" onClick={schliessen} disabled={istSpeichern}>
                Abbrechen
              </Button>
            </div>
          </form>
        ) : null}

        {sammlung.status === "loading" && sammlung.items.length === 0 ? (
          <div className="space-y-2 pb-4" aria-busy="true" aria-label="Prüfungen werden geladen">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : sammlung.items.length === 0 ? (
          <div className="pb-4">
            <EmptyState icon={CheckCircle2} title="Keine Prüfungstermine" description="Trag einen Termin ein, damit du ihn nicht verpasst." />
          </div>
        ) : (
          <div className="-mx-4 border-t border-border">
            {kommend.length > 0 ? <ul className="divide-y divide-border">{kommend.map(zeile)}</ul> : null}

            {vergangen.length > 0 ? (
              <details
                open={vergangenOffen}
                onToggle={(event) => setVergangenOffen(event.currentTarget.open)}
                className="border-t border-border first:border-t-0"
              >
                <summary className="cursor-pointer list-none px-4 py-2.5 text-xs font-medium text-muted-foreground hover:text-foreground">
                  {vergangen.length} vorbei
                </summary>
                <ul className="divide-y divide-border border-t border-border">{vergangen.map(zeile)}</ul>
              </details>
            ) : null}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function PruefungsZeile({
  pruefung,
  fachname,
  reference,
  busy,
  generatorOffen,
  generatorMoeglich,
  laueft,
  onGenerator,
  onEdit,
  onDelete,
}: {
  pruefung: Exam;
  fachname: string | null;
  reference: Date;
  busy: boolean;
  generatorOffen: boolean;
  generatorMoeglich: boolean;
  laueft: boolean;
  onGenerator: () => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const countdown = getCountdown(pruefung.date, reference);
  const bald = !countdown.isOverdue && countdown.days <= 3;
  return (
    <li className="flex items-start gap-3 px-4 py-2.5">
      {/* Datum als Kalenderblatt - dieselbe Form wie bei den Terminen. */}
      <div
        className={cn(
          "flex h-10 w-10 shrink-0 flex-col items-center justify-center rounded-md border border-border",
          countdown.isOverdue && "text-muted-foreground",
          bald && "border-foreground",
        )}
        aria-hidden="true"
      >
        <span className="text-[10px] leading-3 text-muted-foreground">{formatDate(pruefung.date, { month: "short" })}</span>
        <span className="text-[15px] font-semibold leading-5 tabular-nums">{formatDate(pruefung.date, { day: "2-digit" })}</span>
      </div>

      <div className="min-w-0 flex-1">
        <p className={cn("truncate text-[13px] font-medium", countdown.isOverdue && "text-muted-foreground")}>{pruefung.title}</p>
        <p className="mt-0.5 flex min-w-0 flex-wrap items-center gap-x-1.5 text-xs text-muted-foreground">
          {fachname ? (
            <>
              <span className="font-medium text-foreground/80">{fachname}</span>
              <span aria-hidden="true">·</span>
            </>
          ) : null}
          <span className="sr-only">{formatDate(pruefung.date, { weekday: "long", day: "2-digit", month: "long" })},</span>
          <span className={cn(bald && "font-medium text-foreground")}>{countdown.isOverdue ? "Vorbei" : countdown.label}</span>
          {pruefung.source !== "manual" ? (
            <>
              <span aria-hidden="true">·</span>
              <span>Schulportal</span>
            </>
          ) : null}
        </p>
        {pruefung.description ? <p className="mt-0.5 line-clamp-1 text-xs leading-5 text-muted-foreground">{pruefung.description}</p> : null}
      </div>

      {/*
        Der Generator braucht Themen (also ein Fach) und einen Lernplan.
        Fehlt etwas, bleibt der Knopf klickbar und sagt beim Klick, was
        fehlt - ein deaktivierter Knopf zeigt keinen Grund.
      */}
      <div className="flex shrink-0 items-center gap-1.5">
        <Button
          type="button"
          size="icon"
          variant={generatorOffen ? "secondary" : "ghost"}
          onClick={onGenerator}
          disabled={busy || laueft}
          aria-expanded={generatorOffen}
          aria-label={`Lernaufgaben aus den Themen von ${pruefung.title} erzeugen`}
          title="Lernaufgaben aus den Themen erzeugen"
          className={cn(!generatorMoeglich && "text-muted-foreground/60")}
        >
          {laueft ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <ListPlus aria-hidden="true" />}
        </Button>
        <RowActions label={pruefung.title} onEdit={onEdit} onDelete={onDelete} busy={busy} />
      </div>
    </li>
  );
}
