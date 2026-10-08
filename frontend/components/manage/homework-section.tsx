"use client";

import { useMemo, useState, type FormEvent } from "react";
import { AlarmClock, Check, CheckCircle2, LoaderCircle, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Field, FormError } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { RowActions, SectionBanner, SectionHeader } from "@/components/manage/row-actions";
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
import { formatDueDate } from "@/lib/format";
import { useCollection } from "@/lib/use-collection";
import { useSubjectOptions } from "@/lib/use-subject-options";
import { cn } from "@/lib/utils";
import type { Homework } from "@/lib/types";
import { t } from "@/lib/i18n";

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
      setFormularFehler(t("Gib der Aufgabe einen Titel."));
      return;
    }
    if (!formular.faellig) {
      setFormularFehler(t("Wann ist die Aufgabe fällig?"));
      return;
    }
    if (!toUtcIso(formular.faellig)) {
      // Erreicht, wenn der Browser den Wert nicht lesen kann. Ohne diese
      // Pruefung ginge ein unbrauchbares Datum als Text an das Backend.
      setFormularFehler(t("Dieses Datum lässt sich nicht lesen. Bitte erneut wählen."));
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
    <Card id="homework" className={cn(anchorClass, "@container")}>
      <SectionHeader
        title={t("Hausaufgaben")}
        count={t("{n} offen", { n: uebersicht.offen.length })}
        loading={sammlung.status === "loading"}
        onReload={() => void sammlung.reload()}
        formOpen={formularOffen && bearbeitet === null}
        onToggleForm={() => (formularOffen && bearbeitet === null ? schliessen() : oeffnen())}
        newLabel={t("Neue Hausaufgabe")}
      />

      <CardContent className="space-y-3 pb-0">
        <SectionBanner error={sammlung.error} actionError={sammlung.actionError} onRetry={() => void sammlung.reload()} />

        {formularOffen ? (
          <form onSubmit={absenden} noValidate className="space-y-4 rounded-md border border-border bg-muted/40 p-4">
            <div className="flex items-start justify-between gap-3">
              <p className="text-sm font-semibold">{bearbeitet === null ? t("Neue Hausaufgabe") : t("Hausaufgabe bearbeiten")}</p>
              <Button type="button" size="icon" variant="ghost" onClick={schliessen} disabled={istSpeichern} aria-label={t("Formular schließen")}>
                <X aria-hidden="true" />
              </Button>
            </div>

            <Field id="hausaufgabe-titel" label={t("Titel")} required>
              {(props) => (
                <Input
                  {...props}
                  value={formular.titel}
                  onChange={(event) => setFormular((current) => ({ ...current, titel: event.target.value }))}
                  disabled={istSpeichern}
                  placeholder={t("Zum Beispiel: Textanalyse lesen")}
                />
              )}
            </Field>

            <div className="grid gap-4 @md:grid-cols-2">
              <Field id="hausaufgabe-faellig" label={t("Fällig am")} required>
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

              <Field id="hausaufgabe-fach" label={t("Fach")} hint={fach.status === "loading" ? t("Fächer werden geladen …") : undefined}>
                {(props) => (
                  <Select
                    {...props}
                    value={formular.fach}
                    onChange={(event) => setFormular((current) => ({ ...current, fach: event.target.value }))}
                    disabled={istSpeichern}
                  >
                    <option value="">{t("Ohne Fach")}</option>
                    {fach.options.map((option) => (
                      <option key={option.id} value={option.id}>
                        {option.name}
                      </option>
                    ))}
                  </Select>
                )}
              </Field>
            </div>

            <Field id="hausaufgabe-beschreibung" label={t("Notiz")} hint={t("Was genau ist zu tun?")}>
              {(props) => (
                <Textarea
                  {...props}
                  value={formular.beschreibung}
                  onChange={(event) => setFormular((current) => ({ ...current, beschreibung: event.target.value }))}
                  disabled={istSpeichern}
                  placeholder={t("Kapitel 3, Fragen 1 bis 4 beantworten")}
                />
              )}
            </Field>

            <FormError message={formularFehler} />

            <div className="flex flex-wrap items-center gap-2">
              <Button type="submit" disabled={istSpeichern}>
                {istSpeichern ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <CheckCircle2 aria-hidden="true" />}
                {istSpeichern ? t("Wird gespeichert …") : bearbeitet === null ? t("Hausaufgabe anlegen") : t("Änderungen speichern")}
              </Button>
              <Button type="button" variant="ghost" onClick={schliessen} disabled={istSpeichern}>
                {t("Abbrechen")}
              </Button>
            </div>
          </form>
        ) : null}

        {sammlung.status === "loading" && sammlung.items.length === 0 ? (
          <div className="space-y-2 pb-4" aria-busy="true" aria-label={t("Hausaufgaben werden geladen")}>
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : uebersicht.offen.length === 0 && uebersicht.erledigt.length === 0 ? (
          <div className="pb-4">
            <EmptyState icon={CheckCircle2} title={t("Noch keine Hausaufgaben")} description={t("Neue Aufgaben landen direkt in dieser Liste.")} />
          </div>
        ) : (
          <div className="-mx-4 border-t border-border">
            {uebersicht.offen.length > 0 ? (
              <ul className="divide-y divide-border">
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
              <details className="group border-t border-border first:border-t-0">
                <summary className="cursor-pointer list-none px-4 py-2.5 text-xs font-medium text-muted-foreground hover:text-foreground">
                  {uebersicht.erledigt.length} {t("erledigt")}
                </summary>
                <ul className="divide-y divide-border border-t border-border">
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
  // `formatDueDate` sagt "Heute fällig", "Morgen fällig", "Überfällig" -
  // oder liefert nur das Datum. Das Datum bekommt das Wort davor, damit die
  // Zeile nicht zweimal dasselbe Datum zeigt (relativ und absolut).
  const relativ = [t("Überfällig"), t("Heute fällig"), t("Morgen fällig")].includes(faellig);
  const faelligText = relativ ? faellig : t("Fällig {datum}", { datum: faellig });
  const ueberfaellig = faellig === t("Überfällig") && !aufgabe.completed;
  return (
    <li className="flex items-start gap-3 px-4 py-2.5">
      <button
        type="button"
        role="checkbox"
        aria-checked={aufgabe.completed}
        aria-label={aufgabe.completed ? t("{titel} als offen markieren", { titel: aufgabe.title }) : t("{titel} als erledigt markieren", { titel: aufgabe.title })}
        onClick={onToggle}
        disabled={busy}
        className={cn(
          "mt-0.5 flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-full border transition-colors disabled:opacity-50",
          aufgabe.completed ? "border-foreground bg-foreground text-background" : "border-input hover:border-foreground",
        )}
      >
        {aufgabe.completed ? <Check className="h-3 w-3" strokeWidth={3} aria-hidden="true" /> : null}
      </button>

      <div className="min-w-0 flex-1">
        <p className={cn("truncate text-[13px] font-medium", aufgabe.completed && "text-muted-foreground line-through")}>{aufgabe.title}</p>
        <p className="mt-0.5 flex min-w-0 flex-wrap items-center gap-x-1.5 text-xs text-muted-foreground">
          {fachname ? (
            <>
              <span className="font-medium text-foreground/80">{fachname}</span>
              <span aria-hidden="true">·</span>
            </>
          ) : null}
          <span className={cn("inline-flex items-center gap-1", ueberfaellig && "font-medium text-destructive")}>
            <AlarmClock className="h-3 w-3" aria-hidden="true" />
            {faelligText}
          </span>
          {aufgabe.source !== "manual" ? (
            <>
              <span aria-hidden="true">·</span>
              <span>{t("Schulportal")}</span>
            </>
          ) : null}
        </p>
        {aufgabe.description ? <p className="mt-0.5 line-clamp-1 text-xs leading-5 text-muted-foreground">{aufgabe.description}</p> : null}
      </div>

      <RowActions label={aufgabe.title} onEdit={onEdit} onDelete={onDelete} busy={busy} />
    </li>
  );
}
