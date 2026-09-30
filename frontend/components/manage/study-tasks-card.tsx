"use client";

import { useCallback, useRef, useState, type FormEvent } from "react";
import { BookOpen, Check, CheckCircle2, Link2, LoaderCircle, Plus, RefreshCw, X } from "lucide-react";
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
  createStudyTask,
  deleteStudyTask,
  getStudyDependencies,
  getStudyTasks,
  removeStudyDependency,
  updateStudyTask,
} from "@/lib/api";
import { toDateTimeLocal, toUtcIso } from "@/lib/datetime";
import { formatDateTime, formatDuration } from "@/lib/format";
import { bestandenLabel, notenText } from "@/lib/grades";
import { useCollection } from "@/lib/use-collection";
import { useNow } from "@/lib/use-now";
import { cn } from "@/lib/utils";
import { MAX_TASK_PRIORITY, type StudyPlan, type StudyTask, type StudyTaskDependency, type StudyTaskInput, type StudyTaskOrder, type StudyTaskState } from "@/lib/types";

const ZUSTANDS_LABEL: Record<StudyTaskState, string> = {
  pending: "Offen",
  in_progress: "In Arbeit",
  completed: "Erledigt",
  cancelled: "Abgesagt",
};

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

/** `StudyTaskInput` braucht `study_plan_id`; `PATCH` darf es nicht mitschicken. */
type AufgabenAenderung = Omit<StudyTaskInput, "study_plan_id">;

interface AufgabenFormular {
  titel: string;
  beschreibung: string;
  plan: string;
  dauer: string;
  prioritaet: string;
  geplant: string;
  faellig: string;
  zustand: StudyTaskState | "";
}

function ausAufgabe(aufgabe: StudyTask): AufgabenFormular {
  return {
    titel: aufgabe.title,
    beschreibung: aufgabe.description ?? "",
    plan: String(aufgabe.study_plan_id),
    dauer: String(aufgabe.duration_minutes),
    prioritaet: String(aufgabe.priority),
    geplant: toDateTimeLocal(aufgabe.scheduled_for),
    faellig: toDateTimeLocal(aufgabe.due_date),
    zustand: aufgabe.status,
  };
}

/** Liest eine Zahl aus einem Formularfeld und prueft die Grenzen des Backends. */
function zahl(feld: string, min: number, max: number) {
  const wert = Number(feld);
  return Number.isInteger(wert) && wert >= min && wert <= max ? wert : null;
}

/**
 * Die Reihenfolge ist eine Serverfrage, keine Clientfrage.
 *
 * Das Backend kennt `priority`, `due` und `position` und verteilt die
 * Reihenfolge selbst. Eine zweite Sortierung hier waere eine zweite Wahrheit
 * ueber dieselbe Sache - und genau die laeuft auseinander, sobald das Backend
 * eine Regel erweitert.
 */
const ORDNUNG_LABEL: Record<StudyTaskOrder, string> = {
  priority: "Nach Wichtigkeit",
  due: "Nach Frist",
  position: "Nach Reihenfolge",
};

export function StudyTasksCard({ plans, onChanged, refreshKey = 0 }: { plans: StudyPlan[]; onChanged?: () => void; refreshKey?: number }) {
  // `ordnungRef` statt `ordnung`: `useCollection` haelt die Ladefunktion in
  // einer Ref, damit der Ladeeffekt nicht bei jedem Rendern feuert. Die
  // Auswahl aendert sich in einem Ereignishandler, in dem ein
  // Ref-Schreibzugriff erlaubt ist - im Render waere er verboten.
  const ordnungRef = useRef<StudyTaskOrder>("priority");
  const [ordnung, setOrdnung] = useState<StudyTaskOrder>("priority");
  const sammlung = useCollection<StudyTask, StudyTaskInput, AufgabenAenderung>(
    () => getStudyTasks({ order: ordnungRef.current }),
    {
      create: createStudyTask,
      update: (taskId, changes) => updateStudyTask(taskId, changes),
      remove: deleteStudyTask,
    },
    { refreshKey },
  );
  const [formularOffen, setFormularOffen] = useState(false);
  const [bearbeitet, setBearbeitet] = useState<number | null>(null);
  const [formular, setFormular] = useState<AufgabenFormular>({
    titel: "",
    beschreibung: "",
    plan: plans[0] ? String(plans[0].id) : "",
    dauer: "30",
    prioritaet: "2",
    geplant: "",
    faellig: "",
    zustand: "",
  });
  const [formularFehler, setFormularFehler] = useState<string | null>(null);
  /**
   * Der gemeinsame "jetzt" fuer alle Ueberfaellig-Markierungen.
   *
   * `useNow()` liefert erst nach dem Mount eine Zeit. Bis dahin markiert
   * nichts als ueberfaellig - das ist der harmlose Zustand, ein
   * `Date.now()` im Render wuerde die Liste dagegen schon vor dem Mount
   * treiben und ohne jede Aenderung falsch machen.
   */
  const jetzt = useNow()?.getTime() ?? null;

  const istSpeichern = sammlung.pending === "create" || sammlung.pending === `update:${bearbeitet}`;

  function oeffnen(aufgabe?: StudyTask) {
    setBearbeitet(aufgabe?.id ?? null);
    setFormular(
      aufgabe
        ? ausAufgabe(aufgabe)
        : {
            ...formular,
            titel: "",
            beschreibung: "",
            geplant: "",
            faellig: "",
            zustand: "",
            // Nach dem Loeschen eines Plans zeigt das Auswahlfeld sonst eine
            // Id, die es nicht mehr gibt, und das Absenden ergaebe ein 404.
            plan: plans.some((plan) => String(plan.id) === formular.plan) ? formular.plan : (plans[0] ? String(plans[0].id) : ""),
          },
    );
    setFormularFehler(null);
    setFormularOffen(true);
  }

  function schliessen() {
    setFormularOffen(false);
    setBearbeitet(null);
    setFormularFehler(null);
  }

  async function absenden(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!formular.titel.trim()) {
      setFormularFehler("Gib der Lernaufgabe einen Titel.");
      return;
    }
    const dauer = zahl(formular.dauer, 1, 1440);
    if (dauer === null) {
      setFormularFehler("Die Dauer muss zwischen 1 und 1440 Minuten liegen.");
      return;
    }
    const prioritaet = zahl(formular.prioritaet, 0, MAX_TASK_PRIORITY);
    if (prioritaet === null) {
      setFormularFehler(`Die Priorität muss zwischen 0 und ${MAX_TASK_PRIORITY} liegen.`);
      return;
    }
    const geplant = formular.geplant.trim() ? toUtcIso(formular.geplant) : null;
    if (formular.geplant.trim() && !geplant) {
      setFormularFehler("Der geplante Zeitpunkt lässt sich nicht lesen. Bitte erneut wählen.");
      return;
    }
    const faellig = formular.faellig.trim() ? toUtcIso(formular.faellig) : null;
    if (formular.faellig.trim() && !faellig) {
      setFormularFehler("Die Frist lässt sich nicht lesen. Bitte erneut wählen.");
      return;
    }

    setFormularFehler(null);
    const gemeinsam = {
      title: formular.titel.trim(),
      description: formular.beschreibung.trim() || null,
      duration_minutes: dauer,
      priority: prioritaet,
      scheduled_for: geplant,
      due_date: faellig,
    };

    if (bearbeitet !== null) {
      const ergebnis = await sammlung.update(bearbeitet, {
        ...gemeinsam,
        // Ein leeres Auswahlfeld darf den Zustand nicht zuruecksetzen.
        ...(formular.zustand ? { status: formular.zustand } : {}),
      });
      if (ergebnis) {
        schliessen();
        onChanged?.();
      }
      return;
    }

    if (!formular.plan) {
      setFormularFehler("Wähle den Lernplan, zu dem die Aufgabe gehört.");
      return;
    }
    const ergebnis = await sammlung.create({ ...gemeinsam, study_plan_id: Number(formular.plan) });
    if (ergebnis) {
      schliessen();
      onChanged?.();
    }
  }

  return (
    <Card className="border-border/80 bg-card/90">
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-3 space-y-0 pb-4">
        <div>
          <CardTitle>Lernaufgaben</CardTitle>
          <CardDescription className="mt-1">Aufgaben mit Priorität, Frist und Voraussetzungen.</CardDescription>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant="info">
            {sammlung.items.filter((aufgabe) => aufgabe.status === "pending" || aufgabe.status === "in_progress").length} offen
          </Badge>
          <Button type="button" size="icon" variant="ghost" onClick={() => void sammlung.reload()} aria-label="Lernaufgaben neu laden" disabled={sammlung.status === "loading"}>
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

        <label className="flex items-center gap-2 text-xs font-bold text-muted-foreground">
          Sortieren
          <Select
            value={ordnung}
            onChange={(event) => {
              const next = event.target.value as StudyTaskOrder;
              ordnungRef.current = next;
              setOrdnung(next);
              void sammlung.reload();
            }}
            className="h-9 w-48"
            aria-label="Aufgaben sortieren"
          >
            {(Object.keys(ORDNUNG_LABEL) as StudyTaskOrder[]).map((wert) => (
              <option key={wert} value={wert}>
                {ORDNUNG_LABEL[wert]}
              </option>
            ))}
          </Select>
        </label>

        {plans.length === 0 ? (
          <p className="border border-dashed border-border bg-muted/30 px-4 py-3 text-xs leading-5 text-muted-foreground">
            Jede Lernaufgabe gehört zu einem Lernplan. Lege oben zuerst einen Plan an.
          </p>
        ) : null}

        {formularOffen && plans.length > 0 ? (
          <form onSubmit={absenden} noValidate className="space-y-4 rounded-2xl border border-primary/20 bg-primary/5 p-4">
            <div className="flex items-start justify-between gap-3">
              <p className="text-sm font-bold">{bearbeitet === null ? "Neue Lernaufgabe" : "Lernaufgabe bearbeiten"}</p>
              <Button type="button" size="icon" variant="ghost" onClick={schliessen} disabled={istSpeichern} aria-label="Formular schließen">
                <X aria-hidden="true" />
              </Button>
            </div>

            <Field id="aufgabe-titel" label="Titel" required>
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

            {bearbeitet === null ? (
              <Field id="aufgabe-plan" label="Lernplan" required>
                {(props) => (
                  <Select
                    {...props}
                    value={formular.plan}
                    onChange={(event) => setFormular((current) => ({ ...current, plan: event.target.value }))}
                    disabled={istSpeichern}
                  >
                    {plans.map((plan) => (
                      <option key={plan.id} value={plan.id}>
                        {plan.title}
                      </option>
                    ))}
                  </Select>
                )}
              </Field>
            ) : null}

            <div className="grid gap-4 sm:grid-cols-2">
              <Field id="aufgabe-dauer" label="Dauer in Minuten" required>
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

              <Field id="aufgabe-prioritaet" label={`Priorität (0–${MAX_TASK_PRIORITY})`} required hint="Höher bedeutet wichtiger.">
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
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <Field id="aufgabe-geplant" label="Geplant für" hint="Optional. Wann du sie angehst.">
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

              <Field id="aufgabe-faellig" label="Fällig bis" hint="Optional. Bestimmt die Reihenfolge nach Frist.">
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
            </div>

            {bearbeitet !== null ? (
              <Field id="aufgabe-zustand" label="Zustand" hint="Abgesagte Aufgaben bleiben erhalten, zählen aber nicht als offen.">
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

            <Field id="aufgabe-beschreibung" label="Notiz" hint="Optional.">
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

        {sammlung.status === "loading" && sammlung.items.length === 0 ? (
          <div className="space-y-3" aria-busy="true" aria-label="Lernaufgaben werden geladen">
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-24 w-full" />
          </div>
        ) : sammlung.items.length === 0 ? (
          <EmptyState
            icon={BookOpen}
            title="Keine Lernaufgaben"
            description="Lege deine ersten Fokusblöcke an. Einen ganzen Plan aus den Themen einer Prüfung erzeugt die Kachel „Prüfungen“ auf dem Dashboard."
          />
        ) : (
          <ul className="space-y-2.5">
            {sammlung.items.map((aufgabe) => (
              <AufgabenZeile
                key={aufgabe.id}
                aufgabe={aufgabe}
                alle={sammlung.items}
                busy={sammlung.pending !== null}
                now={jetzt}
                onEdit={() => oeffnen(aufgabe)}
                onDelete={async () => {
                  const entfernt = await sammlung.remove(aufgabe.id);
                  if (entfernt) {
                    onChanged?.();
                  }
                }}
                onAenderung={() => onChanged?.()}
              />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

/**
 * Eine Aufgabenzeile mit Voraussetzungen.
 *
 * `blocked_by` kommt direkt aus `GET /study/tasks` mit und genügt für den
 * Hinweis "blockiert durch …" - ein eigener Abruf je Zeile wäre bei drei
 * Dutzend Aufgaben drei Dutzend Requests. Erst das Aufklappen lädt die
 * vollständige Liste der Abhängigkeiten nach.
 */
function AufgabenZeile({
  aufgabe,
  alle,
  busy,
  now,
  onEdit,
  onDelete,
  onAenderung,
}: {
  aufgabe: StudyTask;
  alle: StudyTask[];
  busy: boolean;
  /** Ein festgehaltener Zeitpunkt, damit alle Zeilen dieselbe "jetzt" kennen. */
  now: number | null;
  onEdit: () => void;
  onDelete: () => void;
  onAenderung: () => void;
}) {
  const abhaengigkeiten = useAbhaengigkeiten(aufgabe.id, onAenderung);
  const erledigt = aufgabe.status === "completed";
  // `blocked_by` enthält nur offene Vorbedingungen, das Filtern ist damit
  // doppelt - aber ohne es wuerde ein spaeterer Backend-Wechsel hier
  // stumm Erledigtes als Blocker zeigen.
  const blockiert = aufgabe.blocked_by.filter((voraussetzung) => voraussetzung.status !== "completed" && voraussetzung.status !== "cancelled");
  // Eine abgelaufene Frist ist das Einzige, was den Zustand einer Zeile
  // aendert, ohne dass etwas geklickt wurde. Sie bekommt den gemeinsamen
  // Zeitpunkt der Karte, nicht einen eigenen je Zeile.
  const ueberfaellig =
    now !== null && !erledigt && aufgabe.status !== "cancelled" && aufgabe.due_date !== null && new Date(aufgabe.due_date).getTime() < now;

  return (
    <li className="border border-border bg-card/60 p-3.5">
      <div className="flex items-start gap-3">
        <span className={cn("mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center", erledigt ? "bg-foreground text-background" : "bg-secondary text-secondary-foreground")}>
          {erledigt ? <Check className="h-4 w-4" aria-hidden="true" /> : <BookOpen className="h-4 w-4" aria-hidden="true" />}
        </span>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <p className={cn("truncate text-sm font-extrabold", erledigt && "text-muted-foreground line-through")}>{aufgabe.title}</p>
            <Badge variant={zustandsVariante(aufgabe.status)}>{ZUSTANDS_LABEL[aufgabe.status]}</Badge>
            {aufgabe.priority > 0 ? <Badge variant="warning">Priorität {aufgabe.priority}</Badge> : null}
            {/*
              Der Fachschnitt steht neben der Prioritaet, weil er einer der
              Gruende dafuer sein kann. Eine Prioritaet ohne Herkunft ist eine
              Zahl, die niemand nachfragen kann - mit diesem Hinweis kann man es.
              `null` heisst "zu diesem Fach gibt es keine Noten", nicht 0,0.
            */}
            {aufgabe.subject_average !== null ? (
              <span
                className={cn(
                  "text-[11px] font-bold text-muted-foreground",
                  aufgabe.subject_passed === false && "underline underline-offset-4",
                )}
              >
                Schnitt {notenText(aufgabe.subject_average)} · {bestandenLabel(aufgabe.subject_passed === true)}
              </span>
            ) : null}
          </div>

          {aufgabe.subject_name || aufgabe.topic_name || aufgabe.exam_title ? (
            <p className="mt-1 text-xs font-semibold text-muted-foreground">
              {[aufgabe.subject_name, aufgabe.topic_name, aufgabe.exam_title].filter(Boolean).join(" · ")}
            </p>
          ) : null}
          {aufgabe.description ? <p className="mt-1 line-clamp-2 text-xs leading-5 text-muted-foreground">{aufgabe.description}</p> : null}

          <p className="mt-1.5 text-xs font-semibold text-muted-foreground">
            {formatDuration(aufgabe.duration_minutes)}
            {aufgabe.scheduled_for ? (
              <>
                <span className="text-border"> · </span>
                geplant {formatDateTime(aufgabe.scheduled_for)}
              </>
            ) : null}
            {aufgabe.due_date ? (
              <>
                <span className="text-border"> · </span>
                <span className={cn(ueberfaellig && "text-destructive")}>
                  {ueberfaellig ? "überfällig " : "fällig "}
                  {formatDateTime(aufgabe.due_date)}
                </span>
              </>
            ) : null}
            {aufgabe.completed_at ? (
              <>
                <span className="text-border"> · </span>
                erledigt {formatDateTime(aufgabe.completed_at)}
              </>
            ) : null}
          </p>

          {blockiert.length > 0 ? (
            <p className="mt-2 flex items-start gap-1.5 text-xs font-semibold text-muted-foreground">
              <Link2 className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              <span>
                Blockiert durch {blockiert.map((voraussetzung) => voraussetzung.title).join(", ")}
              </span>
            </p>
          ) : null}
        </div>

        <RowActions label={aufgabe.title} onEdit={onEdit} onDelete={onDelete} busy={busy} />
      </div>

      {/*
        Das native `<details>` laedt beim ersten Aufklappen. `onToggle` feuert
        auch beim Schliessen - deshalb der Vergleich auf `open`, sonst wuerde
        beim Zuklappen erneut geladen.
      */}
      <details
        className="mt-3 border-t border-border/70 pt-3"
        onToggle={(event) => {
          if (event.currentTarget.open) {
            void abhaengigkeiten.laden();
          }
        }}
      >
        <summary className="cursor-pointer list-none text-xs font-bold uppercase tracking-[0.14em] text-muted-foreground hover:text-foreground">
          Voraussetzungen verwalten
        </summary>
        <div className="mt-3">
          <AbhaengigkeitsBereich
            aufgabe={aufgabe}
            alle={alle}
            laeuft={abhaengigkeiten.laeuft}
            fehler={abhaengigkeiten.fehler}
            voraussetzungen={abhaengigkeiten.voraussetzungen}
            onHinzufuegen={abhaengigkeiten.hinzufuegen}
            onEntfernen={abhaengigkeiten.entfernen}
          />
        </div>
      </details>
    </li>
  );
}

/**
 * Laden, Anlegen und Entfernen der Voraussetzungen einer Aufgabe.
 *
 * `StudyTask.blocked_by` nennt nur die **offenen** Voraussetzungen. Fuer die
 * Anzeige braucht man aber auch die erledigten, sonst waere eine Kette nach
 * dem Abhaken unsichtbar und nicht mehr loeschbar. Deshalb wird hier
 * `/dependencies` gelesen und `blocked_by` nur als Kurzfassung im Kopf
 * benutzt.
 */
/**
 * Alle Voraussetzungen einer Aufgabe, nicht nur die offenen.
 *
 * `StudyTask.blocked_by` liefert bewusst **nur offene** Vorbedingungen - das
 * Backend filtert `completed` und `cancelled` heraus, damit Erledigtes nicht
 * ewig blockiert. Zum Verwalten braucht man aber die volle Liste: eine
 * bereits erfuellte Abhaengigkeit, die nur ueber `blocked_by` sichtbar waere,
 * laesst sich nicht mehr entfernen. Deshalb wird `/dependencies` beim
 * Aufklappen nachgeladen - und nur dann, sonst waeren es Dutzend Requests
 * fuer eine Liste, die meistens niemand aufklappt.
 */
function useAbhaengigkeiten(aufgabeId: number, onGeaendert: () => void) {
  const [voraussetzungen, setVoraussetzungen] = useState<StudyTaskDependency[]>([]);
  const [laeuft, setLaeuft] = useState(false);
  const [fehler, setFehler] = useState<string | null>(null);

  const laden = useCallback(async () => {
    setLaeuft(true);
    setFehler(null);
    try {
      setVoraussetzungen(await getStudyDependencies(aufgabeId));
    } catch (ladeFehler) {
      setFehler(ladeFehler instanceof Error ? ladeFehler.message : "Die Voraussetzungen konnten nicht geladen werden.");
    } finally {
      setLaeuft(false);
    }
  }, [aufgabeId]);

  const hinzufuegen = useCallback(
    async (dependsOnTaskId: number) => {
      setLaeuft(true);
      setFehler(null);
      try {
        const angelegt = await addStudyDependency(aufgabeId, dependsOnTaskId);
        setVoraussetzungen((current) =>
          current.some((eintrag) => eintrag.depends_on_task_id === dependsOnTaskId) ? current : [...current, angelegt],
        );
        onGeaendert();
      } catch (hinzuFehler) {
        // Ein Zyklus kommt vom Backend als 422 mit eigener Meldung. Die wird
        // durchgereicht statt hier erfunden - sie kennt den Grund.
        setFehler(hinzuFehler instanceof Error ? hinzuFehler.message : "Die Voraussetzung konnte nicht gesetzt werden.");
      } finally {
        setLaeuft(false);
      }
    },
    [aufgabeId, onGeaendert],
  );

  const entfernen = useCallback(
    async (dependsOnTaskId: number) => {
      setLaeuft(true);
      setFehler(null);
      try {
        await removeStudyDependency(aufgabeId, dependsOnTaskId);
        setVoraussetzungen((current) => current.filter((eintrag) => eintrag.depends_on_task_id !== dependsOnTaskId));
        onGeaendert();
      } catch (entfernFehler) {
        setFehler(entfernFehler instanceof Error ? entfernFehler.message : "Die Voraussetzung konnte nicht entfernt werden.");
      } finally {
        setLaeuft(false);
      }
    },
    [aufgabeId, onGeaendert],
  );

  return { voraussetzungen, laeuft, fehler, laden, hinzufuegen, entfernen };
}

function AbhaengigkeitsBereich({
  aufgabe,
  alle,
  laeuft,
  fehler,
  voraussetzungen,
  onHinzufuegen,
  onEntfernen,
}: {
  aufgabe: StudyTask;
  alle: StudyTask[];
  laeuft: boolean;
  fehler: string | null;
  voraussetzungen: StudyTaskDependency[];
  onHinzufuegen: (id: number) => void;
  onEntfernen: (id: number) => void;
}) {
  const [auswahl, setAuswahl] = useState("");

  // Kandidaten: alles aus demselben Plan, ausser sich selbst und die, die
  // schon Voraussetzung sind. Das Backend lehnt Zyklen und Verbindungen ueber
  // Plangrenzen mit 422 ab - beides vorab auszuschliessen erspart die Meldung.
  const planId = aufgabe.study_plan_id;
  const gesetzt = new Set(voraussetzungen.map((eintrag) => eintrag.depends_on_task_id));
  const kandidaten = alle.filter((kandidat) => kandidat.study_plan_id === planId && kandidat.id !== aufgabe.id && !gesetzt.has(kandidat.id));

  const titelVon = (taskId: number) => alle.find((kandidat) => kandidat.id === taskId)?.title ?? `Aufgabe ${taskId}`;

  return (
    <div className="space-y-2.5">
      <p className="text-xs font-bold uppercase tracking-[0.14em] text-muted-foreground">Voraussetzungen</p>

      {laeuft && voraussetzungen.length === 0 ? (
        <Skeleton className="h-8 w-full" />
      ) : voraussetzungen.length === 0 ? (
        <p className="text-xs text-muted-foreground">Diese Aufgabe kann ohne Vorbedingung gestartet werden.</p>
      ) : (
        <ul className="space-y-1.5">
          {voraussetzungen.map((voraussetzung) => {
            const quelle = alle.find((kandidat) => kandidat.id === voraussetzung.depends_on_task_id);
            const erfuellt = quelle?.status === "completed";
            return (
              <li key={voraussetzung.depends_on_task_id} className="flex items-center gap-2 border border-border bg-muted/30 px-2.5 py-1.5 text-xs">
                <span className={cn("min-w-0 flex-1 truncate font-semibold", erfuellt && "text-muted-foreground line-through")}>
                  {titelVon(voraussetzung.depends_on_task_id)}
                </span>
                <Badge variant={erfuellt ? "success" : "secondary"}>{erfuellt ? "Erledigt" : "Offen"}</Badge>
                <Button
                  type="button"
                  size="icon"
                  variant="ghost"
                  disabled={laeuft}
                  onClick={() => onEntfernen(voraussetzung.depends_on_task_id)}
                  aria-label={`Voraussetzung ${titelVon(voraussetzung.depends_on_task_id)} entfernen`}
                >
                  <X className="h-4 w-4" aria-hidden="true" />
                </Button>
              </li>
            );
          })}
        </ul>
      )}

      {kandidaten.length > 0 ? (
        <div className="flex flex-wrap items-end gap-2">
          <div className="min-w-[12rem] flex-1 space-y-1.5">
            <label htmlFor={`voraussetzung-${aufgabe.id}`} className="block text-xs font-bold">
              Neue Voraussetzung
            </label>
            <Select id={`voraussetzung-${aufgabe.id}`} value={auswahl} onChange={(event) => setAuswahl(event.target.value)} disabled={laeuft}>
              <option value="">Bitte wählen</option>
              {kandidaten.map((kandidat) => (
                <option key={kandidat.id} value={kandidat.id}>
                  {kandidat.title}
                </option>
              ))}
            </Select>
          </div>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={laeuft || !auswahl}
            onClick={() => {
              onHinzufuegen(Number(auswahl));
              setAuswahl("");
            }}
          >
            {laeuft ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <Link2 aria-hidden="true" />}
            Hinzufügen
          </Button>
        </div>
      ) : alle.length > 1 ? (
        <p className="text-xs leading-5 text-muted-foreground">
          Keine weitere Aufgabe in diesem Plan, die als Voraussetzung taugt. Das Backend lässt Abhängigkeiten nur innerhalb eines Plans zu.
        </p>
      ) : null}

      <FormError message={fehler} />
    </div>
  );
}

