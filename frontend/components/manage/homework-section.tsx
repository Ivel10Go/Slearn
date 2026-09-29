"use client";

import { useMemo, useState, type FormEvent } from "react";
import { AlarmClock, BookOpen, CheckCircle2, LoaderCircle, Plus, RefreshCw, X } from "lucide-react";
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
  createHomework,
  deleteHomework,
  getHomework,
  setHomeworkCompleted,
  updateHomework,
  type HomeworkChanges,
  type HomeworkWrite,
} from "@/lib/api";
import { toDateTimeLocal, toUtcIso } from "@/lib/datetime";
import { formatDate, formatDueDate } from "@/lib/format";
import { useCollection } from "@/lib/use-collection";
import { useSubjectOptions } from "@/lib/use-subject-options";
import { cn } from "@/lib/utils";
import type { Homework } from "@/lib/types";

/** Wie `lib/dashboard-view` den Sprungmarken-Freiraum haelt. */
const anchorClass = "scroll-mt-24";

/** Der Formularzustand in genau der Form, wie das Eingabefeld ihn haelt. */
interface HausaufgabenFormular {
  titel: string;
  beschreibung: string;
  faellig: string;
  fach: string;
}

const leeresFormular: HausaufgabenFormular = { titel: "", beschreibung: "", faellig: "", fach: "" };

function ausAufgabe(aufgabe: Homework): HausaufgabenFormular {
  return {
    titel: aufgabe.title,
    beschreibung: aufgabe.description ?? "",
    // `toDateTimeLocal` ist der Umkehrweg von `toUtcIso`: das Feld zeigt die
    // Uhrzeit, die der Browser auch anzeigen wuerde, sonst springt ein
    // unveraenderter Termin beim Speichern um die Zonendifferenz.
    faellig: toDateTimeLocal(aufgabe.due_date),
    fach: aufgabe.subject_id === null ? "" : String(aufgabe.subject_id),
  };
}

function alsEingabe(formular: HausaufgabenFormular): HomeworkWrite {
  const faellig = toUtcIso(formular.faellig);
  return {
    title: formular.titel.trim(),
    due_date: faellig ?? formular.faellig,
    description: formular.beschreibung.trim() || null,
    subject_id: formular.fach ? Number(formular.fach) : null,
  };
}

function alsAenderung(formular: HausaufgabenFormular): HomeworkChanges {
  const faellig = toUtcIso(formular.faellig);
  return {
    title: formular.titel.trim(),
    due_date: faellig ?? formular.faellig,
    description: formular.beschreibung.trim() || null,
    subject_id: formular.fach ? Number(formular.fach) : null,
  };
}

export function HomeworkSection({ reference }: { reference: Date }) {
  const fach = useSubjectOptions();
  const sammlung = useCollection<Homework, HomeworkWrite, HomeworkChanges>(
    () => getHomework(),
    { create: createHomework, update: updateHomework, remove: deleteHomework },
    // Das Backend liefert nach `due_date` sortiert. Beim Anlegen wird deshalb
    // an die passende Stelle einsortiert, statt unten angehaengt.
    { sort: (a, b) => a.due_date.localeCompare(b.due_date) || a.id - b.id },
  );
  const [formularOffen, setFormularOffen] = useState(false);
  const [bearbeitet, setBearbeitet] = useState<number | null>(null);
  const [formular, setFormular] = useState<HausaufgabenFormular>(leeresFormular);
  const [formularFehler, setFormularFehler] = useState<string | null>(null);

  const uebersicht = useMemo(
    () => ({
      offen: sammlung.items.filter((item) => !item.completed),
      erledigt: sammlung.items.filter((item) => item.completed),
    }),
    [sammlung.items],
  );

  const istSpeichern = sammlung.pending === "create" || sammlung.pending === `update:${bearbeitet}`;

  function oeffnen(aufgabe?: Homework) {
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
      setFormularFehler("Gib der Aufgabe einen Titel.");
      return;
    }
    if (!formular.faellig) {
      setFormularFehler("Wann ist die Aufgabe fällig?");
      return;
    }
    if (!toUtcIso(formular.faellig)) {
      // Erreicht, wenn der Browser den Wert nicht lesen kann. Ohne diese
      // Pruefung ginge ein unbrauchbares Datum als Text an das Backend.
      setFormularFehler("Dieses Datum lässt sich nicht lesen. Bitte erneut wählen.");
      return;
    }

    setFormularFehler(null);
    const ergebnis = bearbeitet === null ? await sammlung.create(alsEingabe(formular)) : await sammlung.update(bearbeitet, alsAenderung(formular));
    if (ergebnis) {
      schliessen();
    }
  }

  /**
   * Der Haken.
   *
   * Bewusst `setHomeworkCompleted` und nicht die schnellere Toggle-Route: der
   * Aufrufer sagt, **was** gelten soll. Ein Toggle macht aus einem Klick
   * "erledigt" beim naechsten "offen", auch wenn sich dazwischen jemand anders
   * die Liste angeschaut hat.
   *
   * `patch` uebernimmt genau das Objekt, das der Server zurueckgibt, statt die
   * Liste neu zu laden - sonst springt die Sortierung, und die gerade
   * abgehakte Zeile wandert unter den Ladeblink.
   */
  async function abhaken(aufgabe: Homework) {
    await sammlung.mutate(`completed:${aufgabe.id}`, () => setHomeworkCompleted(aufgabe.id, !aufgabe.completed));
  }

  return (
    <Card id="homework" className={anchorClass}>
      <CardHeader className="flex-row flex-wrap items-start justify-between space-y-0 pb-4">
        <div>
          <CardTitle className="flex items-center gap-2 text-lg">
            <BookOpen className="h-5 w-5 text-primary" aria-hidden="true" />
            Hausaufgaben
          </CardTitle>
          <CardDescription className="mt-1">Was auf deinem Schreibtisch wartet – und was schon erledigt ist.</CardDescription>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant={uebersicht.offen.length ? "warning" : "success"}>{uebersicht.offen.length} offen</Badge>
          <Button type="button" size="icon" variant="ghost" onClick={() => void sammlung.reload()} aria-label="Hausaufgaben neu laden" disabled={sammlung.status === "loading"}>
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

        {formularOffen ? (
          <form onSubmit={absenden} noValidate className="space-y-4 rounded-2xl border border-primary/20 bg-primary/5 p-4">
            <div className="flex items-start justify-between gap-3">
              <p className="text-sm font-bold">{bearbeitet === null ? "Neue Hausaufgabe" : "Hausaufgabe bearbeiten"}</p>
              <Button type="button" size="icon" variant="ghost" onClick={schliessen} disabled={istSpeichern} aria-label="Formular schließen">
                <X aria-hidden="true" />
              </Button>
            </div>

            <Field id="hausaufgabe-titel" label="Titel" required>
              {(props) => (
                <Input
                  {...props}
                  value={formular.titel}
                  onChange={(event) => setFormular((current) => ({ ...current, titel: event.target.value }))}
                  disabled={istSpeichern}
                  placeholder="Zum Beispiel: Textanalyse lesen"
                />
              )}
            </Field>

            <div className="grid gap-4 sm:grid-cols-2">
              <Field id="hausaufgabe-faellig" label="Fällig am" required>
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

              <Field id="hausaufgabe-fach" label="Fach" hint={fach.status === "loading" ? "Fächer werden geladen …" : undefined}>
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

            <Field id="hausaufgabe-beschreibung" label="Notiz" hint="Was genau ist zu tun?">
              {(props) => (
                <Textarea
                  {...props}
                  value={formular.beschreibung}
                  onChange={(event) => setFormular((current) => ({ ...current, beschreibung: event.target.value }))}
                  disabled={istSpeichern}
                  placeholder="Kapitel 3, Fragen 1 bis 4 beantworten"
                />
              )}
            </Field>

            <FormError message={formularFehler} />

            <div className="flex flex-wrap items-center gap-2">
              <Button type="submit" disabled={istSpeichern}>
                {istSpeichern ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <CheckCircle2 aria-hidden="true" />}
                {istSpeichern ? "Wird gespeichert …" : bearbeitet === null ? "Hausaufgabe anlegen" : "Änderungen speichern"}
              </Button>
              <Button type="button" variant="ghost" onClick={schliessen} disabled={istSpeichern}>
                Abbrechen
              </Button>
            </div>
          </form>
        ) : null}

        {sammlung.status === "loading" && sammlung.items.length === 0 ? (
          <div className="space-y-3" aria-busy="true" aria-label="Hausaufgaben werden geladen">
            <Skeleton className="h-20 w-full" />
            <Skeleton className="h-20 w-full" />
          </div>
        ) : uebersicht.offen.length === 0 && uebersicht.erledigt.length === 0 ? (
          <EmptyState icon={CheckCircle2} title="Noch keine Hausaufgaben" description="Lege deine erste Aufgabe an – sie landet direkt in dieser Liste." />
        ) : (
          <div className="space-y-4">
            {uebersicht.offen.length > 0 ? (
              <ul className="space-y-2.5">
                {uebersicht.offen.map((aufgabe) => (
                  <AufgabenZeile
                    key={aufgabe.id}
                    aufgabe={aufgabe}
                    fachname={aufgabe.subject_name}
                    reference={reference}
                    busy={sammlung.pending !== null}
                    onToggle={() => void abhaken(aufgabe)}
                    onEdit={() => oeffnen(aufgabe)}
                    onDelete={() => sammlung.remove(aufgabe.id)}
                  />
                ))}
              </ul>
            ) : null}

            {uebersicht.erledigt.length > 0 ? (
              <details className="group">
                <summary className="cursor-pointer list-none text-xs font-bold uppercase tracking-[0.14em] text-muted-foreground">
                  {uebersicht.erledigt.length} erledigt
                </summary>
                <ul className="mt-3 space-y-2.5">
                  {uebersicht.erledigt.map((aufgabe) => (
                    <AufgabenZeile
                      key={aufgabe.id}
                      aufgabe={aufgabe}
                      fachname={aufgabe.subject_name}
                      reference={reference}
                      busy={sammlung.pending !== null}
                      onToggle={() => void abhaken(aufgabe)}
                      onEdit={() => oeffnen(aufgabe)}
                      onDelete={() => sammlung.remove(aufgabe.id)}
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

function AufgabenZeile({
  aufgabe,
  fachname,
  reference,
  busy,
  onToggle,
  onEdit,
  onDelete,
}: {
  aufgabe: Homework;
  fachname: string | null;
  reference: Date;
  busy: boolean;
  onToggle: () => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const faellig = formatDueDate(aufgabe.due_date, reference);
  return (
    <li className="flex items-start gap-3 border border-border bg-card/60 p-3.5 transition-colors hover:border-foreground/40">
      <button
        type="button"
        role="checkbox"
        aria-checked={aufgabe.completed}
        aria-label={`${aufgabe.title} als ${aufgabe.completed ? "offen" : "erledigt"} markieren`}
        onClick={onToggle}
        disabled={busy}
        className={cn(
          "mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center border transition-colors disabled:opacity-50",
          aufgabe.completed ? "border-foreground bg-foreground text-background" : "border-border bg-muted hover:border-foreground",
        )}
      >
        {aufgabe.completed ? <CheckCircle2 className="h-4 w-4" aria-hidden="true" /> : <span className="sr-only">Nicht erledigt</span>}
      </button>

      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <p className={cn("truncate text-sm font-extrabold", aufgabe.completed && "text-muted-foreground line-through")}>{aufgabe.title}</p>
          {fachname ? <span className="text-xs font-semibold text-primary">{fachname}</span> : null}
        </div>
        {aufgabe.description ? <p className="mt-1 line-clamp-2 text-xs leading-5 text-muted-foreground">{aufgabe.description}</p> : null}
        <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs font-semibold text-muted-foreground">
          <AlarmClock className="h-3.5 w-3.5" aria-hidden="true" />
          <span className={cn(faellig === "Überfällig" && !aufgabe.completed && "text-destructive")}>{faellig}</span>
          <span className="text-border">·</span>
          <span>{formatDate(aufgabe.due_date, { day: "2-digit", month: "short" })}</span>
          {aufgabe.source !== "manual" ? (
            <>
              <span className="text-border">·</span>
              <span>aus dem Schulportal</span>
            </>
          ) : null}
        </div>
      </div>

      <RowActions label={aufgabe.title} onEdit={onEdit} onDelete={onDelete} busy={busy} />
    </li>
  );
}
