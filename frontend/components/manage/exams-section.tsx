"use client";

import { useState, type FormEvent } from "react";
import { CalendarDays, CheckCircle2, ListPlus, LoaderCircle, Plus, RefreshCw, TrendingUp, X } from "lucide-react";
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

  return (
    <Card id="exams" className={anchorClass}>
      <CardHeader className="flex-row flex-wrap items-start justify-between space-y-0 pb-4">
        <div>
          <CardTitle className="flex items-center gap-2 text-lg">
            <TrendingUp className="h-5 w-5 text-primary" aria-hidden="true" />
            Prüfungen
          </CardTitle>
          <CardDescription className="mt-1">Alle Termine mit Countdown – auch die, die schon vorbei sind.</CardDescription>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant={kommend.length ? "warning" : "success"}>{kommend.length} anstehend</Badge>
          <Button type="button" size="icon" variant="ghost" onClick={() => void sammlung.reload()} aria-label="Prüfungen neu laden" disabled={sammlung.status === "loading"}>
            <RefreshCw className={cn(sammlung.status === "loading" && "animate-spin")} aria-hidden="true" />
          </Button>
          <Button type="button" size="sm" onClick={() => (formularOffen && bearbeitet === null ? schliessen() : oeffnen())} aria-expanded={formularOffen && bearbeitet === null}>
            {formularOffen && bearbeitet === null ? <X aria-hidden="true" /> : <Plus aria-hidden="true" />}
            {formularOffen && bearbeitet === null ? "Schließen" : "Neu"}
          </Button>
        </div>
      </CardHeader>

      <CardContent className="space-y-4">
        <SectionBanner error={sammlung.error} actionError={sammlung.actionError} onRetry={() => void sammlung.reload()} />

        {/*
          Aus den Klausurthemen einen Lernplan bauen. Bisher verlinkte das
          Dashboard die Themen nur; jetzt entsteht daraus eine Aufgabe je Thema.
        */}
        {planErgebnis ? (
          <p className="rounded-2xl border border-foreground/20 bg-foreground/5 px-4 py-3 text-sm font-semibold" role="status">
            {planErgebnis.created_count} Aufgaben angelegt
            {planErgebnis.skipped_count > 0 ? `, ${planErgebnis.skipped_count} Thema übersprungen` : ""}
            {planErgebnis.last_due_on ? ` · bis ${formatDate(planErgebnis.last_due_on)}` : ""}. Sie stehen in der Kachel{" "}
            <span className="font-extrabold">Lernaufgaben</span>.
          </p>
        ) : null}
        <FormError message={planFehler} />

        {plaene.items.length === 0 && sammlung.items.length > 0 ? (
          <p className="border border-dashed border-border px-4 py-3 text-xs leading-5 text-muted-foreground">
            Für den Generator braucht es einen Lernplan. Lege in der Kachel{" "}
            <span className="font-extrabold">Lernaufgaben</span> einen an.
          </p>
        ) : null}

        {zielPruefung !== null ? (
          <div className="space-y-4 border border-primary/20 bg-primary/5 p-4">
            <p className="text-sm font-bold">Lernplan aus den Themen erzeugen</p>
            <div className="grid gap-4 sm:grid-cols-2">
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
          <form onSubmit={absenden} noValidate className="space-y-4 rounded-2xl border border-primary/20 bg-primary/5 p-4">
            <div className="flex items-start justify-between gap-3">
              <p className="text-sm font-bold">{bearbeitet === null ? "Neue Prüfung" : "Prüfung bearbeiten"}</p>
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

            <div className="grid gap-4 sm:grid-cols-2">
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
          <div className="space-y-3" aria-busy="true" aria-label="Prüfungen werden geladen">
            <Skeleton className="h-20 w-full" />
            <Skeleton className="h-20 w-full" />
          </div>
        ) : sammlung.items.length === 0 ? (
          <EmptyState icon={CheckCircle2} title="Keine Prüfungstermine" description="Trag einen Termin ein, damit du ihn nicht verpasst." />
        ) : (
          <div className="space-y-4">
            <ul className="space-y-2.5">
              {kommend.map((pruefung) => (
                <PruefungsZeile
                  key={pruefung.id}
                  pruefung={pruefung}
                  fachname={pruefung.subject_name}
                  reference={reference}
                  busy={sammlung.pending !== null}
                  generatorOffen={zielPruefung === pruefung.id}
                  generatorMoeglich={plaene.items.length > 0 && pruefung.subject_id !== null}
                  laueft={laueftAuf === pruefung.id}
                  onGenerator={() => {
                    setPlanFehler(null);
                    setPlanErgebnis(null);
                    setZielPruefung((aktuell) => (aktuell === pruefung.id ? null : pruefung.id));
                    setZielPlan("");
                  }}
                  onEdit={() => oeffnen(pruefung)}
                  onDelete={() => sammlung.remove(pruefung.id)}
                />
              ))}
            </ul>

            {vergangen.length > 0 ? (
              <details open={vergangenOffen} onToggle={(event) => setVergangenOffen(event.currentTarget.open)}>
                <summary className="cursor-pointer list-none text-xs font-bold uppercase tracking-[0.14em] text-muted-foreground">
                  {vergangen.length} vorbei
                </summary>
                <ul className="mt-3 space-y-2.5">
                  {vergangen.map((pruefung) => (
                    <PruefungsZeile
                      key={pruefung.id}
                      pruefung={pruefung}
                      fachname={pruefung.subject_name}
                      reference={reference}
                      busy={sammlung.pending !== null}
                      generatorOffen={zielPruefung === pruefung.id}
                      generatorMoeglich={plaene.items.length > 0 && pruefung.subject_id !== null}
                      laueft={laueftAuf === pruefung.id}
                      onGenerator={() => {
                        setPlanFehler(null);
                        setPlanErgebnis(null);
                        setZielPruefung((aktuell) => (aktuell === pruefung.id ? null : pruefung.id));
                        setZielPlan("");
                      }}
                      onEdit={() => oeffnen(pruefung)}
                      onDelete={() => sammlung.remove(pruefung.id)}
                    />
                  ))}
                </ul>
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
  return (
    <li className="flex items-start gap-3 border border-border bg-card/60 p-3.5 transition-colors hover:border-foreground/40">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            {fachname ? <p className="text-xs font-bold uppercase tracking-[0.14em] text-primary">{fachname}</p> : null}
            <p className={cn("mt-1 truncate text-sm font-extrabold", countdown.isOverdue && "text-muted-foreground")}>
              {pruefung.title}
            </p>
          </div>
          <Badge variant={countdown.isOverdue ? "outline" : countdown.days <= 3 ? "warning" : "info"}>
            {countdown.isOverdue ? "Vorbei" : countdown.label}
          </Badge>
        </div>

        {pruefung.description ? (
          <p className="mt-2 line-clamp-3 text-xs leading-5 text-muted-foreground">{pruefung.description}</p>
        ) : null}

        <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs font-semibold text-muted-foreground">
          <CalendarDays className="h-3.5 w-3.5" aria-hidden="true" />
          <span>{formatDate(pruefung.date, { weekday: "short", day: "2-digit", month: "short", year: "numeric" })}</span>
          {pruefung.source !== "manual" ? (
            <>
              <span className="text-border">·</span>
              <span>aus dem Schulportal</span>
            </>
          ) : null}
        </div>

        {/*
          Der Generator braucht Themen, und Themen haengen an einem Fach.
          Ohne Fach gibt es nichts zu erzeugen - deshalb ist der Knopf dann
          nicht nur deaktiviert, sondern sagt auch warum.
        */}
        <div className="mt-2">
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={onGenerator}
            disabled={busy || laueft || !generatorMoeglich}
            aria-expanded={generatorOffen}
            title={pruefung.subject_id === null ? "Diese Klausur hat kein Fach, also keine Themen." : undefined}
          >
            {laueft ? (
              <LoaderCircle className="animate-spin" aria-hidden="true" />
            ) : (
              <ListPlus className="h-3.5 w-3.5" aria-hidden="true" />
            )}
            {generatorOffen ? "Abbrechen" : "Aufgaben aus Themen"}
          </Button>
          {pruefung.subject_id === null ? (
            <span className="ml-2 text-xs text-muted-foreground">
              Kein Fach hinterlegt – ohne Themen gibt es nichts zu erzeugen.
            </span>
          ) : null}
        </div>
      </div>

      <RowActions label={pruefung.title} onEdit={onEdit} onDelete={onDelete} busy={busy} />
    </li>
  );
}
