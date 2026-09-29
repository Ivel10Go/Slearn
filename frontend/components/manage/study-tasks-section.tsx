"use client";

import { useRef, useState, type FormEvent } from "react";
import {
  AlarmClock,
  BookOpen,
  CalendarClock,
  Check,
  CheckCircle2,
  Clock3,
  Link2,
  Link2Off,
  ListChecks,
  LoaderCircle,
  Plus,
  RefreshCw,
  Repeat,
  Timer,
  Trash2,
  Unlink,
  X,
  Zap,
} from "lucide-react";
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
import {
  addStudyDependency,
  cancelStudySeries,
  createStudyPlan,
  createStudyTask,
  createStudySeries,
  deleteStudyPlan,
  deleteStudyTask,
  getStudyPlans,
  getStudySeries,
  getStudyTasks,
  removeStudyDependency,
  renameStudyPlan,
  updateStudySeries,
  updateStudyTask,
  type StudyTaskChanges,
  type StudyTaskWrite,
} from "@/lib/api";
import { toDateTimeLocal, toUtcIso } from "@/lib/datetime";
import { formatDateTime, formatDuration, formatDueDate } from "@/lib/format";
import { errorMessage, useCollection } from "@/lib/use-collection";
import { useNow } from "@/lib/use-now";
import { cn } from "@/lib/utils";
import type { StudyPlan, StudyTask, StudyTaskRecurrence, StudyTaskSeries, StudyTaskSeriesInput, StudyTaskState } from "@/lib/types";

/** Wie die uebrigen Sektionen den Sprungmarken-Freiraum haelt. */
const anchorClass = "scroll-mt-24";

/** Die Aenderungen, die eine Serie waehrend ihrer Laufzeit bekommen darf. */
type StudySeriesChanges = Pick<
  StudyTaskSeriesInput,
  "title" | "description" | "duration_minutes" | "priority" | "until" | "occurrence_limit"
>;

const ZUSTANDS_LABEL: Record<StudyTaskState, string> = {
  pending: "Offen",
  in_progress: "In Arbeit",
  completed: "Erledigt",
  cancelled: "Abgesagt",
};

const SERIENTYP_LABEL: Record<StudyTaskRecurrence, string> = {
  daily: "täglich",
  weekly: "wöchentlich",
  biweekly: "alle zwei Wochen",
  monthly: "monatlich",
};

type Ordnung = "priority" | "due" | "position";

const ORDNUNG_LABEL: Record<Ordnung, string> = {
  priority: "Nach Wichtigkeit",
  due: "Nach Fälligkeit",
  position: "Nach Reihenfolge",
};

/** Spiegelt `MAX_PRIORITY` im Backend. */
const MAX_PRIO = 5;

function zustandsVariante(zustand: StudyTaskState) {
  if (zustand === "completed") {
    return "success" as const;
  }
  if (zustand === "in_progress") {
    return "info" as const;
  }
  if (zustand === "cancelled") {
    return "outline" as const;
  }
  return "secondary" as const;
}

/**
 * Fuenf Bloecke statt einer Zahl.
 *
 * Die Oberflaeche ist monochrome, also hebt sich eine Zahl nicht von jeder
 * anderen Zahl ab. Fuenf gefuellte Quadrate sind auf einen Blick lesbar; die
 * Zahl steht fuer Screenreader daneben, weil Balken nichts ansagen.
 */
function PrioritaetsMarke({ wert }: { wert: number }) {
  const niveau = Math.min(MAX_PRIO, Math.max(0, wert));
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="flex items-center gap-px" aria-hidden="true">
        {Array.from({ length: MAX_PRIO }, (_, index) => (
          <span key={index} className={cn("h-3 w-1.5", index < niveau ? "bg-foreground" : "bg-border")} />
        ))}
      </span>
      <span className="sr-only">Wichtigkeit {niveau} von {MAX_PRIO}</span>
    </span>
  );
}

/** Zwischen 1 und 1440 Minuten, wie es das Backend verlangt. */
function pruefeDauer(wert: string): number | null {
  const zahl = Number(wert);
  return Number.isInteger(zahl) && zahl >= 1 && zahl <= 1440 ? zahl : null;
}

function pruefePrioritaet(wert: string): number | null {
  const zahl = Number(wert);
  return Number.isInteger(zahl) && zahl >= 0 && zahl <= MAX_PRIO ? zahl : null;
}

interface AufgabenFormular {
  titel: string;
  beschreibung: string;
  dauer: string;
  geplant: string;
  faellig: string;
  prioritaet: string;
  zustand: StudyTaskState | "";
}

const leeresFormular: AufgabenFormular = {
  titel: "",
  beschreibung: "",
  dauer: "25",
  geplant: "",
  faellig: "",
  prioritaet: "0",
  zustand: "",
};

function ausAufgabe(aufgabe: StudyTask): AufgabenFormular {
  return {
    titel: aufgabe.title,
    beschreibung: aufgabe.description ?? "",
    dauer: String(aufgabe.duration_minutes),
    // `toDateTimeLocal` ist der Umkehrweg von `toUtcIso`: das Feld zeigt die
    // Uhrzeit, die der Browser auch anzeigen wuerde, sonst springt ein
    // unveraenderter Termin beim Speichern um die Zonendifferenz.
    geplant: toDateTimeLocal(aufgabe.scheduled_for),
    faellig: toDateTimeLocal(aufgabe.due_date),
    prioritaet: String(aufgabe.priority),
    zustand: aufgabe.status,
  };
}

export function StudyTasksSection() {
  const ordnungRef = useRef<Ordnung>("priority");
  const [ordnung, setOrdnung] = useState<Ordnung>("priority");
  const [planId, setPlanId] = useState<number | null>(null);
  const [formularOffen, setFormularOffen] = useState(false);
  const [bearbeitet, setBearbeitet] = useState<number | null>(null);
  const [formular, setFormular] = useState<AufgabenFormular>(leeresFormular);
  const [formularFehler, setFormularFehler] = useState<string | null>(null);
  const [verknuepftMit, setVerknuepftMit] = useState<number | null>(null);
  const [neuerPlanTitel, setNeuerPlanTitel] = useState("");

  const plaene = useCollection<StudyPlan, { title: string }, { title: string }>(() => getStudyPlans(), {
    create: (input) => createStudyPlan(input),
    update: (id, changes) => renameStudyPlan(id, changes.title),
    remove: (id) => deleteStudyPlan(id),
  });

  const aufgaben = useCollection<StudyTask, StudyTaskWrite, StudyTaskChanges>(
    // `ordnungRef` statt `ordnung`: `useCollection` haelt die Ladefunktion in
    // einer Ref, damit der Ladeeffekt nicht bei jedem Rendern feuert. Die
    // Auswahl aendert sich in einem Ereignishandler, in dem ein
    // Ref-Schreibzugriff erlaubt ist - im Render waere er verboten.
    () => getStudyTasks({ order: ordnungRef.current }),
    { create: createStudyTask, update: updateStudyTask, remove: deleteStudyTask },
  );

  const serien = useCollection<StudyTaskSeries, StudyTaskSeriesInput, StudySeriesChanges>(() => getStudySeries(), {
    create: createStudySeries,
    update: updateStudySeries,
    remove: cancelStudySeries,
  });

  const aktiverPlan = plaene.items.find((plan) => plan.id === planId) ?? null;
  const sichtbar = planId === null ? aufgaben.items : aufgaben.items.filter((aufgabe) => aufgabe.study_plan_id === planId);
  const offen = sichtbar.filter((aufgabe) => aufgabe.status !== "completed" && aufgabe.status !== "cancelled");
  const istSpeichern = aufgaben.pending === "create" || aufgaben.pending === `update:${bearbeitet}`;
  const laueft = aufgaben.pending !== null || plaene.pending !== null || serien.pending !== null;

  /**
   * Die Reihenfolge ist eine Serverfrage, keine Clientfrage.
   *
   * Das Backend kennt `priority`, `due` und `position` und verteilt die
   * Reihenfolge selbst. Eine zweite Sortierung hier waere eine zweite Wahrheit
   * ueber dieselbe Sache - und genau die laeuft auseinander, sobald das
   * Backend eine Regel erweitert.
   */
  function sortieren(next: Ordnung) {
    ordnungRef.current = next;
    setOrdnung(next);
    void aufgaben.reload();
  }

  function allesNeuLaden() {
    void aufgaben.reload();
    void serien.reload();
  }

  function oeffnen(aufgabe?: StudyTask) {
    setBearbeitet(aufgabe?.id ?? null);
    setFormular(aufgabe ? ausAufgabe(aufgabe) : leeresFormular);
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
      setFormularFehler("Gib der Lernaufgabe einen Titel.");
      return;
    }
    const dauer = pruefeDauer(formular.dauer);
    if (dauer === null) {
      setFormularFehler("Die Dauer muss zwischen 1 und 1440 Minuten liegen.");
      return;
    }
    const prioritaet = pruefePrioritaet(formular.prioritaet);
    if (prioritaet === null) {
      setFormularFehler(`Die Wichtigkeit muss zwischen 0 und ${MAX_PRIO} liegen.`);
      return;
    }
    for (const [feld, wert] of [
      ["Geplant für", formular.geplant],
      ["Fällig am", formular.faellig],
    ] as const) {
      // Erreicht, wenn der Browser den Wert nicht lesen kann. Ohne diese
      // Pruefung ginge ein unbrauchbares Datum als Text an das Backend.
      if (wert.trim() && !toUtcIso(wert)) {
        setFormularFehler(`${feld}: Dieses Datum lässt sich nicht lesen. Bitte erneut wählen.`);
        return;
      }
    }
    const zielPlan =
      bearbeitet === null ? planId : (aufgaben.items.find((aufgabe) => aufgabe.id === bearbeitet)?.study_plan_id ?? null);
    if (zielPlan === null) {
      // Stand hier einmal "das Backend hat keine Plan-Endpunkte". Jetzt gibt es
      // sie, und der Weg fuehrt ueber den Plan-Kopf dieser Kachel.
      setFormularFehler("Wähle zuerst einen Lernplan aus oder lege einen an.");
      return;
    }

    setFormularFehler(null);
    const gemeinsam = {
      title: formular.titel.trim(),
      duration_minutes: dauer,
      description: formular.beschreibung.trim() || null,
      scheduled_for: formular.geplant.trim() ? toUtcIso(formular.geplant) : null,
      due_date: formular.faellig.trim() ? toUtcIso(formular.faellig) : null,
      priority: prioritaet,
    };
    const ergebnis =
      bearbeitet === null
        ? await aufgaben.create({ study_plan_id: zielPlan, ...gemeinsam })
        : await aufgaben.update(bearbeitet, {
            ...gemeinsam,
            // Nur mitsenden, wenn etwas gewaehlt wurde: ein leeres Feld darf
            // den Zustand nicht auf `undefined` zuruecksetzen.
            ...(formular.zustand ? { status: formular.zustand } : {}),
          });
    if (ergebnis) {
      schliessen();
    }
  }

  /**
   * Verknuepfungen aendern die Liste, also wird danach neu gelesen.
   *
   * Das Backend berechnet `blocked_by` beim Lesen. Eine lokal gepatchte Aufgabe
   * zeigte eine Abhaengigkeit, die es in der Datenbank nicht gibt - und die
   * Warteschlange waere genau dann falsch, wenn sie zaehlt.
   */
  async function verknuepfen(aufgabeId: number, voraussetzungId: number) {
    setVerknuepftMit(aufgabeId);
    try {
      await addStudyDependency(aufgabeId, voraussetzungId);
      await aufgaben.reload();
    } catch (fehler) {
      setFormularFehler(errorMessage(fehler, "Die Verknüpfung hat nicht geklappt."));
    } finally {
      setVerknuepftMit(null);
    }
  }

  async function trennen(aufgabeId: number, voraussetzungId: number) {
    setVerknuepftMit(aufgabeId);
    try {
      await removeStudyDependency(aufgabeId, voraussetzungId);
      await aufgaben.reload();
    } catch (fehler) {
      setFormularFehler(errorMessage(fehler, "Die Verknüpfung ließ sich nicht lösen."));
    } finally {
      setVerknuepftMit(null);
    }
  }

  async function planAnlegen(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const titel = neuerPlanTitel.trim();
    if (!titel) {
      return;
    }
    const plan = await plaene.create({ title: titel });
    if (plan) {
      setNeuerPlanTitel("");
      setPlanId(plan.id);
    }
  }

  return (
    <Card id="study-tasks" className={anchorClass}>
      <CardHeader className="flex-row flex-wrap items-start justify-between space-y-0 pb-4">
        <div>
          <CardTitle className="flex items-center gap-2 text-lg">
            <Zap className="h-5 w-5 text-primary" aria-hidden="true" />
            Lernaufgaben
          </CardTitle>
          <CardDescription className="mt-1">
            Fokusblöcke zum Abhaken – mit Wichtigkeit, Fälligkeit und einer Reihenfolge, die gilt.
          </CardDescription>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant={offen.length ? "warning" : "success"}>{offen.length} offen</Badge>
          <Button
            type="button"
            size="icon"
            variant="ghost"
            onClick={allesNeuLaden}
            aria-label="Lernaufgaben neu laden"
            disabled={aufgaben.status === "loading"}
          >
            <RefreshCw className={cn(aufgaben.status === "loading" && "animate-spin")} aria-hidden="true" />
          </Button>
          <Button
            type="button"
            size="sm"
            onClick={() => (formularOffen && bearbeitet === null ? schliessen() : oeffnen())}
            aria-expanded={formularOffen && bearbeitet === null}
            disabled={plaene.items.length === 0}
            title={plaene.items.length === 0 ? "Lege zuerst einen Lernplan an." : undefined}
          >
            {formularOffen && bearbeitet === null ? <X aria-hidden="true" /> : <Plus aria-hidden="true" />}
            {formularOffen && bearbeitet === null ? "Schließen" : "Neu"}
          </Button>
        </div>
      </CardHeader>

      <CardContent className="space-y-5">
        <SectionBanner
          error={aufgaben.error ?? plaene.error ?? serien.error}
          actionError={aufgaben.actionError ?? plaene.actionError ?? serien.actionError}
          onRetry={allesNeuLaden}
        />

        <PlanKopf
          plaene={plaene.items}
          aktiverPlan={aktiverPlan}
          planId={planId}
          neuerPlanTitel={neuerPlanTitel}
          busy={plaene.pending !== null}
          onSelect={setPlanId}
          onTitel={setNeuerPlanTitel}
          onAnlegen={planAnlegen}
          onUmbenennen={(id, titel) => void plaene.update(id, { title: titel })}
          onLoeschen={(id) => void plaene.remove(id)}
        />

        <div className="flex flex-wrap items-center justify-between gap-3">
          <label className="flex items-center gap-2 text-xs font-bold text-muted-foreground">
            Sortieren
            <Select
              value={ordnung}
              onChange={(event) => sortieren(event.target.value as Ordnung)}
              className="h-9 w-48"
              aria-label="Aufgaben sortieren"
            >
              {(Object.keys(ORDNUNG_LABEL) as Ordnung[]).map((wert) => (
                <option key={wert} value={wert}>
                  {ORDNUNG_LABEL[wert]}
                </option>
              ))}
            </Select>
          </label>
          <p className="text-xs text-muted-foreground">
            {serien.items.filter((serie) => serie.status === "active").length} laufende Serien
          </p>
        </div>

        {formularOffen ? (
          <form onSubmit={absenden} noValidate className="space-y-4 rounded-2xl border border-primary/20 bg-primary/5 p-4">
            <div className="flex items-start justify-between gap-3">
              <p className="text-sm font-bold">{bearbeitet === null ? "Neue Lernaufgabe" : "Lernaufgabe bearbeiten"}</p>
              <Button type="button" size="icon" variant="ghost" onClick={schliessen} disabled={istSpeichern} aria-label="Formular schließen">
                <X aria-hidden="true" />
              </Button>
            </div>

            <Field id="lernaufgabe-titel" label="Titel" required>
              {(props) => (
                <Input
                  {...props}
                  value={formular.titel}
                  onChange={(event) => setFormular((current) => ({ ...current, titel: event.target.value }))}
                  disabled={istSpeichern}
                  placeholder="Zum Beispiel: Ableitungen üben"
                />
              )}
            </Field>

            <div className="grid gap-4 sm:grid-cols-2">
              <Field id="lernaufgabe-dauer" label="Dauer in Minuten" required hint="Zwischen 1 und 1440.">
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

              <Field id="lernaufgabe-prioritaet" label="Wichtigkeit" hint={`0 bis ${MAX_PRIO}. Höher steht die Aufgabe weiter vorn.`}>
                {(props) => (
                  <Select
                    {...props}
                    value={formular.prioritaet}
                    onChange={(event) => setFormular((current) => ({ ...current, prioritaet: event.target.value }))}
                    disabled={istSpeichern}
                  >
                    {Array.from({ length: MAX_PRIO + 1 }, (_, stufe) => (
                      <option key={stufe} value={stufe}>
                        {stufe === 0 ? "0 – normal" : stufe}
                      </option>
                    ))}
                  </Select>
                )}
              </Field>

              <Field id="lernaufgabe-faellig" label="Fällig am" hint="Bis wann sie erledigt sein soll.">
                {(props) => (
                  <Input
                    {...props}
                    type="datetime-local"
                    value={formular.faellig}
                    onChange={(event) => setFormular((current) => ({ ...current, faellig: event.target.value }))}
                    disabled={istSpeichern}
                  />
                )}
              </Field>

              <Field id="lernaufgabe-geplant" label="Geplant für" hint="Optional. Ohne Angabe steht die Aufgabe ohne Termin da.">
                {(props) => (
                  <Input
                    {...props}
                    type="datetime-local"
                    value={formular.geplant}
                    onChange={(event) => setFormular((current) => ({ ...current, geplant: event.target.value }))}
                    disabled={istSpeichern}
                  />
                )}
              </Field>
            </div>

            {bearbeitet !== null ? (
              <Field id="lernaufgabe-zustand" label="Zustand" hint="Abgesagte Aufgaben bleiben erhalten, zählen aber nicht als offen.">
                {(props) => (
                  <Select
                    {...props}
                    value={formular.zustand}
                    onChange={(event) => setFormular((current) => ({ ...current, zustand: event.target.value as StudyTaskState }))}
                    disabled={istSpeichern}
                  >
                    {(["pending", "in_progress", "completed", "cancelled"] as const).map((zustand) => (
                      <option key={zustand} value={zustand}>
                        {ZUSTANDS_LABEL[zustand]}
                      </option>
                    ))}
                  </Select>
                )}
              </Field>
            ) : null}

            <Field id="lernaufgabe-beschreibung" label="Notiz">
              {(props) => (
                <Textarea
                  {...props}
                  value={formular.beschreibung}
                  onChange={(event) => setFormular((current) => ({ ...current, beschreibung: event.target.value }))}
                  disabled={istSpeichern}
                  placeholder="Aufgaben 12 und 14, danach Karteikarten"
                />
              )}
            </Field>

            <FormError message={formularFehler} />

            <div className="flex flex-wrap items-center gap-2">
              <Button type="submit" disabled={istSpeichern}>
                {istSpeichern ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <CheckCircle2 aria-hidden="true" />}
                {istSpeichern ? "Wird gespeichert …" : bearbeitet === null ? "Lernaufgabe anlegen" : "Änderungen speichern"}
              </Button>
              <Button type="button" variant="ghost" onClick={schliessen} disabled={istSpeichern}>
                Abbrechen
              </Button>
            </div>
          </form>
        ) : null}

        {aufgaben.status === "loading" && aufgaben.items.length === 0 ? (
          <div className="space-y-3" aria-busy="true" aria-label="Lernaufgaben werden geladen">
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-24 w-full" />
          </div>
        ) : sichtbar.length === 0 ? (
          <EmptyState
            icon={Timer}
            title="Keine Lernaufgaben"
            description={
              plaene.items.length === 0
                ? "Lege oben einen Lernplan an – Aufgaben und Serien hängen daran."
                : "Hier entstehen deine Fokusblöcke. Mit „Neu“ legst du die erste an."
            }
          />
        ) : (
          <ul className="space-y-2.5">
            {sichtbar.map((aufgabe) => (
              <AufgabenZeile
                key={aufgabe.id}
                aufgabe={aufgabe}
                kandidaten={sichtbar.filter((andere) => andere.id !== aufgabe.id)}
                busy={laueft}
                verknuepftMit={verknuepftMit === aufgabe.id}
                onEdit={() => oeffnen(aufgabe)}
                onDelete={() => void aufgaben.remove(aufgabe.id)}
                onVerknuepfen={(voraussetzung) => void verknuepfen(aufgabe.id, voraussetzung)}
                onTrennen={(voraussetzung) => void trennen(aufgabe.id, voraussetzung)}
              />
            ))}
          </ul>
        )}

        <SerienBereich
          serien={serien.items}
          planId={planId}
          busy={serien.pending !== null}
          status={serien.status}
          onAnlegen={(eingabe) => void serien.create(eingabe)}
          onBeenden={(serieId) => void serien.remove(serieId)}
        />

        {/*
          Start und Stopp liegen bewusst **nicht** hier.
          `POST /study/tasks/{id}/start` legt eine Lernsession an, und der
          Fortschritt gehoert in die Kachel "Heute lernen" auf dem Dashboard.
          Hier steht die Verwaltung, dort der Ablauf.
        */}
        {sichtbar.length > 0 ? (
          <p className="border-t border-border/70 pt-3 text-xs leading-5 text-muted-foreground">
            <Trash2 className="mr-1 inline h-3.5 w-3.5" aria-hidden="true" />
            Eine blockierte Aufgabe lässt sich nicht starten, solange ihre Voraussetzung offen ist. Lernsessions startest du in der
            Kachel <span className="font-semibold">Heute lernen</span> auf dem Dashboard.
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}

function PlanKopf({
  plaene,
  aktiverPlan,
  planId,
  neuerPlanTitel,
  busy,
  onSelect,
  onTitel,
  onAnlegen,
  onUmbenennen,
  onLoeschen,
}: {
  plaene: StudyPlan[];
  aktiverPlan: StudyPlan | null;
  planId: number | null;
  neuerPlanTitel: string;
  busy: boolean;
  onSelect: (id: number | null) => void;
  onTitel: (titel: string) => void;
  onAnlegen: (event: FormEvent<HTMLFormElement>) => void;
  onUmbenennen: (id: number, titel: string) => void;
  onLoeschen: (id: number) => void;
}) {
  const [umbenennen, setUmbenennen] = useState(false);
  const [titel, setTitel] = useState("");

  return (
    <div className="space-y-3 border border-border bg-card/60 p-3.5">
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-2 text-xs font-bold text-muted-foreground">
          <ListChecks className="h-3.5 w-3.5" aria-hidden="true" />
          Lernplan
          <Select
            value={planId === null ? "" : String(planId)}
            onChange={(event) => onSelect(event.target.value ? Number(event.target.value) : null)}
            className="h-9 min-w-52"
            aria-label="Lernplan auswählen"
          >
            <option value="">Alle Pläne</option>
            {plaene.map((plan) => (
              <option key={plan.id} value={plan.id}>
                {plan.title} ({plan.open_task_count} offen)
              </option>
            ))}
          </Select>
        </label>

        {aktiverPlan ? (
          <div className="flex items-center gap-1.5">
            {umbenennen ? (
              <>
                <Input
                  value={titel}
                  onChange={(event) => setTitel(event.target.value)}
                  disabled={busy}
                  aria-label="Neuer Name des Lernplans"
                  className="h-9 w-48"
                />
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={busy || !titel.trim()}
                  onClick={() => {
                    onUmbenennen(aktiverPlan.id, titel.trim());
                    setUmbenennen(false);
                  }}
                >
                  <Check aria-hidden="true" />
                  Speichern
                </Button>
                <Button
                  type="button"
                  size="icon"
                  variant="ghost"
                  onClick={() => setUmbenennen(false)}
                  disabled={busy}
                  aria-label="Umbenennen abbrechen"
                >
                  <X aria-hidden="true" />
                </Button>
              </>
            ) : (
              <>
                <Button
                  type="button"
                  size="icon"
                  variant="ghost"
                  onClick={() => {
                    setTitel(aktiverPlan.title);
                    setUmbenennen(true);
                  }}
                  disabled={busy}
                  aria-label="Lernplan umbenennen"
                >
                  <BookOpen aria-hidden="true" />
                </Button>
                <Button
                  type="button"
                  size="icon"
                  variant="ghost"
                  onClick={() => onLoeschen(aktiverPlan.id)}
                  disabled={busy}
                  aria-label="Lernplan löschen"
                >
                  <Trash2 aria-hidden="true" />
                </Button>
              </>
            )}
          </div>
        ) : null}
      </div>

      {plaene.length === 0 ? (
        <form onSubmit={onAnlegen} className="flex flex-wrap items-end gap-2">
          <Field
            id="lernplan-neu"
            label="Neuer Lernplan"
            hint="Aufgaben und Serien brauchen einen Plan, dem sie angehören."
            className="min-w-56 flex-1"
          >
            {(props) => (
              <Input
                {...props}
                value={neuerPlanTitel}
                onChange={(event) => onTitel(event.target.value)}
                disabled={busy}
                placeholder="Zum Beispiel: Klausur Analysis"
              />
            )}
          </Field>
          <Button type="submit" size="sm" disabled={busy || !neuerPlanTitel.trim()}>
            {busy ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <Plus aria-hidden="true" />}
            Plan anlegen
          </Button>
        </form>
      ) : null}
    </div>
  );
}

function AufgabenZeile({
  aufgabe,
  kandidaten,
  busy,
  verknuepftMit,
  onEdit,
  onDelete,
  onVerknuepfen,
  onTrennen,
}: {
  aufgabe: StudyTask;
  kandidaten: StudyTask[];
  busy: boolean;
  verknuepftMit: boolean;
  onEdit: () => void;
  onDelete: () => void;
  onVerknuepfen: (voraussetzungId: number) => void;
  onTrennen: (voraussetzungId: number) => void;
}) {
  const [verknuepfenOffen, setVerknuepfenOffen] = useState(false);
  // `Date.now()` direkt beim Rendern ist impure: unter nebenlaeufigem Rendern
  // kann React die Zeile verwerfen und neu berechnen, und das Ergebnis haengt
  // dann vom Zufall ab. `useNow` liefert dieselbe Zeit fuer alle Zeilen und
  // aktualisiert sie im Sekundentakt - so wechselt die Markierung von selbst,
  // wenn die Frist ablaeuft.
  const jetzt = useNow();
  const erledigt = aufgabe.status === "completed";
  const abgesagt = aufgabe.status === "cancelled";
  const laeuft = aufgabe.status === "in_progress";
  const ueberfaellig =
    !erledigt && !abgesagt && aufgabe.due_date !== null && new Date(aufgabe.due_date).getTime() < (jetzt?.getTime() ?? 0);
  // Eine bereits erledigte Voraussetzung steht nicht mehr in `blocked_by`,
  // weil sie nichts mehr blockiert. Sie darf deshalb auch nicht neu gesetzt
  // werden - sonst entstuende eine Kette ohne Nutzen.
  const moeglich = kandidaten.filter(
    (andere) => !aufgabe.blocked_by.some((warte) => warte.task_id === andere.id) && andere.status !== "completed",
  );

  return (
    <li className={cn("border p-3.5 transition-colors", laeuft ? "border-foreground bg-muted" : "border-border bg-card/60")}>
      <div className="flex items-start gap-3">
        <span
          className={cn(
            "mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center",
            erledigt ? "bg-foreground text-background" : "bg-secondary text-secondary-foreground",
          )}
        >
          {erledigt ? <Check className="h-4 w-4" aria-hidden="true" /> : <BookOpen className="h-4 w-4" aria-hidden="true" />}
        </span>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <p className={cn("truncate text-sm font-extrabold", (erledigt || abgesagt) && "text-muted-foreground line-through")}>
              {aufgabe.title}
            </p>
            <Badge variant={zustandsVariante(aufgabe.status)}>{ZUSTANDS_LABEL[aufgabe.status]}</Badge>
            {aufgabe.priority > 0 ? <PrioritaetsMarke wert={aufgabe.priority} /> : null}
            {aufgabe.series_id ? (
              <Badge variant="outline">
                <Repeat className="h-3 w-3" aria-hidden="true" />
                {aufgabe.series_recurrence ? SERIENTYP_LABEL[aufgabe.series_recurrence] : "Serie"}
              </Badge>
            ) : null}
          </div>

          {aufgabe.subject_name || aufgabe.topic_name || aufgabe.exam_title ? (
            <p className="mt-1 text-xs font-semibold text-muted-foreground">
              {[aufgabe.subject_name, aufgabe.topic_name, aufgabe.exam_title ? `für ${aufgabe.exam_title}` : null]
                .filter(Boolean)
                .join(" · ")}
            </p>
          ) : null}
          {aufgabe.description ? (
            <p className="mt-1 line-clamp-2 text-xs leading-5 text-muted-foreground">{aufgabe.description}</p>
          ) : null}

          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs font-semibold text-muted-foreground">
            <span className="inline-flex items-center gap-1.5">
              <Clock3 className="h-3.5 w-3.5" aria-hidden="true" />
              {formatDuration(aufgabe.duration_minutes)}
            </span>
            {aufgabe.due_date ? (
              <span className={cn("inline-flex items-center gap-1.5", ueberfaellig && "text-destructive")}>
                <AlarmClock className="h-3.5 w-3.5" aria-hidden="true" />
                fällig {formatDueDate(aufgabe.due_date)}
              </span>
            ) : null}
            {aufgabe.scheduled_for ? (
              <span className="inline-flex items-center gap-1.5">
                <CalendarClock className="h-3.5 w-3.5" aria-hidden="true" />
                geplant {formatDateTime(aufgabe.scheduled_for)}
              </span>
            ) : null}
            {aufgabe.completed_at ? <span>erledigt {formatDateTime(aufgabe.completed_at)}</span> : null}
          </div>

          {aufgabe.blocked_by.length > 0 ? (
            <p className="mt-2 border border-foreground/20 bg-foreground/5 px-2.5 py-1.5 text-xs font-semibold">
              <Link2 className="mr-1 inline h-3.5 w-3.5" aria-hidden="true" />
              Wartet auf: {aufgabe.blocked_by.map((warte) => warte.title).join(", ")}
            </p>
          ) : null}

          <div className="mt-2">
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => setVerknuepfenOffen((offen) => !offen)}
              aria-expanded={verknuepfenOffen}
            >
              <Link2 className="h-3.5 w-3.5" aria-hidden="true" />
              {verknuepfenOffen ? "Voraussetzungen schließen" : "Voraussetzung wählen"}
            </Button>

            {verknuepfenOffen ? (
              <div className="mt-2 space-y-2 border border-dashed border-border p-2.5">
                {aufgabe.blocked_by.length > 0 ? (
                  <ul className="space-y-1">
                    {aufgabe.blocked_by.map((warte) => (
                      <li key={warte.task_id} className="flex items-center justify-between gap-2 text-xs font-semibold">
                        <span className="truncate">{warte.title}</span>
                        <Button
                          type="button"
                          size="icon"
                          variant="ghost"
                          onClick={() => onTrennen(warte.task_id)}
                          disabled={busy}
                          aria-label={`Voraussetzung ${warte.title} entfernen`}
                        >
                          <Unlink aria-hidden="true" />
                        </Button>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-xs text-muted-foreground">Diese Aufgabe wartet auf nichts.</p>
                )}

                {moeglich.length > 0 ? (
                  <Select
                    value=""
                    onChange={(event) => {
                      if (event.target.value) {
                        onVerknuepfen(Number(event.target.value));
                      }
                    }}
                    disabled={busy || verknuepftMit}
                    aria-label="Aufgabe als Voraussetzung wählen"
                  >
                    <option value="">{verknuepftMit ? "Wird gespeichert …" : "Aufgabe als Voraussetzung wählen …"}</option>
                    {moeglich.map((andere) => (
                      <option key={andere.id} value={andere.id}>
                        {andere.title}
                      </option>
                    ))}
                  </Select>
                ) : null}
              </div>
            ) : null}
          </div>
        </div>

        <RowActions label={aufgabe.title} onEdit={onEdit} onDelete={onDelete} busy={busy} />
      </div>
    </li>
  );
}

interface SerienFormular {
  titel: string;
  intervall: StudyTaskRecurrence;
  dauer: string;
  prioritaet: string;
  start: string;
  ende: string;
  anzahl: string;
}

const leeresSerienFormular: SerienFormular = {
  titel: "",
  intervall: "weekly",
  dauer: "15",
  prioritaet: "0",
  start: "",
  ende: "",
  anzahl: "",
};

function SerienBereich({
  serien,
  planId,
  busy,
  status,
  onAnlegen,
  onBeenden,
}: {
  serien: StudyTaskSeries[];
  planId: number | null;
  busy: boolean;
  status: "loading" | "ready" | "error";
  onAnlegen: (eingabe: StudyTaskSeriesInput) => void;
  onBeenden: (serieId: number) => void;
}) {
  const [offen, setOffen] = useState(false);
  const [formular, setFormular] = useState<SerienFormular>(leeresSerienFormular);
  const [fehler, setFehler] = useState<string | null>(null);
  const aktive = serien.filter((serie) => serie.status !== "cancelled");

  function absenden(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!formular.titel.trim()) {
      setFehler("Gib der Serie einen Titel.");
      return;
    }
    const dauer = pruefeDauer(formular.dauer);
    if (dauer === null) {
      setFehler("Die Dauer muss zwischen 1 und 1440 Minuten liegen.");
      return;
    }
    const prioritaet = pruefePrioritaet(formular.prioritaet);
    if (prioritaet === null) {
      setFehler(`Die Wichtigkeit muss zwischen 0 und ${MAX_PRIO} liegen.`);
      return;
    }
    const start = toUtcIso(formular.start);
    if (!formular.start.trim()) {
      setFehler("Ab wann soll die Serie laufen?");
      return;
    }
    if (!start) {
      setFehler("Das Startdatum lässt sich nicht lesen.");
      return;
    }
    const ende = formular.ende.trim() ? toUtcIso(formular.ende) : null;
    if (formular.ende.trim() && !ende) {
      setFehler("Das Enddatum lässt sich nicht lesen.");
      return;
    }
    if (ende && ende < start) {
      setFehler("Das Enddatum liegt vor dem Start.");
      return;
    }
    const anzahl = formular.anzahl.trim() ? Number(formular.anzahl) : null;
    if (anzahl !== null && (!Number.isInteger(anzahl) || anzahl < 1 || anzahl > 1000)) {
      setFehler("Die Anzahl muss zwischen 1 und 1000 liegen.");
      return;
    }
    // Faellt kein Plan aus der Auswahl, wandert die Serie in den, in dem die
    // zuletzt gesehene Aufgabe liegt. Sonst gaebe es kein Ziel, und eine
    // leere Pflichtangabe waere nur eine weitere Fehlermeldung.
    const zielPlan = planId ?? serien[serien.length - 1]?.study_plan_id ?? null;
    if (zielPlan === null) {
      setFehler("Wähle oben einen Lernplan aus – eine Serie gehört in einen Plan.");
      return;
    }

    setFehler(null);
    onAnlegen({
      study_plan_id: zielPlan,
      title: formular.titel.trim(),
      recurrence: formular.intervall,
      starts_on: start,
      duration_minutes: dauer,
      priority: prioritaet,
      until: ende,
      occurrence_limit: anzahl,
    });
    setFormular(leeresSerienFormular);
    setOffen(false);
  }

  return (
    <section className="space-y-3 border-t border-border/70 pt-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="flex items-center gap-2 text-sm font-extrabold">
            <Repeat className="h-4 w-4" aria-hidden="true" />
            Serien
          </h3>
          <p className="mt-0.5 text-xs leading-5 text-muted-foreground">
            Vokabeln, Wiederholungsblätter: Es entsteht immer nur die nächste Aufgabe. Erst wenn du sie abhakst, kommt die
            nächste dazu.
          </p>
        </div>
        <Button type="button" size="sm" variant="outline" onClick={() => setOffen((wert) => !wert)} aria-expanded={offen}>
          {offen ? <X aria-hidden="true" /> : <Plus aria-hidden="true" />}
          {offen ? "Schließen" : "Neue Serie"}
        </Button>
      </div>

      {offen ? (
        <form onSubmit={absenden} noValidate className="space-y-4 border border-primary/20 bg-primary/5 p-4">
          <Field id="serie-titel" label="Titel" required>
            {(props) => (
              <Input
                {...props}
                value={formular.titel}
                onChange={(event) => setFormular((current) => ({ ...current, titel: event.target.value }))}
                disabled={busy}
                placeholder="Zum Beispiel: Vokabeln Liste 3"
              />
            )}
          </Field>

          <div className="grid gap-4 sm:grid-cols-2">
            <Field id="serie-intervall" label="Wiederholung" required>
              {(props) => (
                <Select
                  {...props}
                  value={formular.intervall}
                  onChange={(event) => setFormular((current) => ({ ...current, intervall: event.target.value as StudyTaskRecurrence }))}
                  disabled={busy}
                >
                  {(Object.keys(SERIENTYP_LABEL) as StudyTaskRecurrence[]).map((typ) => (
                    <option key={typ} value={typ}>
                      {SERIENTYP_LABEL[typ]}
                    </option>
                  ))}
                </Select>
              )}
            </Field>

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
                  disabled={busy}
                />
              )}
            </Field>

            <Field id="serie-start" label="Erste Fälligkeit" required hint="Die erste Aufgabe entsteht sofort.">
              {(props) => (
                <Input
                  {...props}
                  type="datetime-local"
                  value={formular.start}
                  onChange={(event) => setFormular((current) => ({ ...current, start: event.target.value }))}
                  disabled={busy}
                />
              )}
            </Field>

            <Field id="serie-ende" label="Letzte Fälligkeit" hint="Optional. Danach endet die Serie von selbst.">
              {(props) => (
                <Input
                  {...props}
                  type="datetime-local"
                  value={formular.ende}
                  onChange={(event) => setFormular((current) => ({ ...current, ende: event.target.value }))}
                  disabled={busy}
                />
              )}
            </Field>

            <Field id="serie-anzahl" label="Höchstens Aufgaben" hint="Optional. Eine Obergrenze zusätzlich zum Enddatum.">
              {(props) => (
                <Input
                  {...props}
                  type="number"
                  min={1}
                  max={1000}
                  value={formular.anzahl}
                  onChange={(event) => setFormular((current) => ({ ...current, anzahl: event.target.value }))}
                  disabled={busy}
                  placeholder="z. B. 10"
                />
              )}
            </Field>

            <Field id="serie-prioritaet" label="Wichtigkeit" hint={`0 bis ${MAX_PRIO}.`}>
              {(props) => (
                <Select
                  {...props}
                  value={formular.prioritaet}
                  onChange={(event) => setFormular((current) => ({ ...current, prioritaet: event.target.value }))}
                  disabled={busy}
                >
                  {Array.from({ length: MAX_PRIO + 1 }, (_, stufe) => (
                    <option key={stufe} value={stufe}>
                      {stufe === 0 ? "0 – normal" : stufe}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
          </div>

          <FormError message={fehler} />

          <div className="flex flex-wrap items-center gap-2">
            <Button type="submit" disabled={busy}>
              {busy ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <Plus aria-hidden="true" />}
              Serie anlegen
            </Button>
            <Button type="button" variant="ghost" onClick={() => setOffen(false)} disabled={busy}>
              Abbrechen
            </Button>
          </div>
        </form>
      ) : null}

      {status === "loading" && serien.length === 0 ? (
        <Skeleton className="h-16 w-full" />
      ) : aktive.length === 0 ? (
        <p className="text-xs text-muted-foreground">Noch keine Serie angelegt.</p>
      ) : (
        <ul className="space-y-2">
          {aktive.map((serie) => (
            <li key={serie.id} className="flex flex-wrap items-center justify-between gap-3 border border-border bg-card/60 p-3">
              <div className="min-w-0">
                <p className="truncate text-sm font-bold">{serie.title}</p>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {SERIENTYP_LABEL[serie.recurrence]} · {serie.occurrences} erzeugt
                  {serie.next_due_on ? ` · nächste ${formatDateTime(serie.next_due_on)}` : " · zu Ende"}
                </p>
              </div>
              <div className="flex items-center gap-2">
                {serie.status === "active" ? <Badge variant="info">Läuft</Badge> : <Badge variant="outline">Beendet</Badge>}
                {serie.status === "active" ? (
                  <Button type="button" size="sm" variant="outline" onClick={() => onBeenden(serie.id)} disabled={busy}>
                    <Link2Off className="h-3.5 w-3.5" aria-hidden="true" />
                    Beenden
                  </Button>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
