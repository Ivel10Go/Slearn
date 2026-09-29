"use client";

import { useState, type FormEvent } from "react";
import { CalendarDays, CheckCircle2, Clock3, LoaderCircle, Plus, RefreshCw, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Field, FormError } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { RowActions, SectionBanner } from "@/components/manage/row-actions";
import {
  createCalendarEvent,
  deleteCalendarEvent,
  getCalendarEvents,
  updateCalendarEvent,
  type CalendarEventChanges,
  type CalendarEventWrite,
} from "@/lib/api";
import { toDateInput, toDateTimeLocal, toUtcIsoFromParts } from "@/lib/datetime";
import { formatDate, formatTime, isSameDay, toIsoDate } from "@/lib/format";
import { useCollection } from "@/lib/use-collection";
import { cn } from "@/lib/utils";
import type { CalendarEvent } from "@/lib/types";

const anchorClass = "scroll-mt-24";

interface KalenderFormular {
  titel: string;
  beschreibung: string;
  /** Ganztagestermin: nur der Tag, keine Uhrzeit. */
  ganztag: boolean;
  tag: string;
  von: string;
  bis: string;
}

const leeresFormular: KalenderFormular = {
  titel: "",
  beschreibung: "",
  ganztag: false,
  tag: toIsoDate(new Date()),
  von: "09:00",
  bis: "10:00",
};

function ausTermin(termin: CalendarEvent): KalenderFormular {
  // Ein Ganztagestermin kommt mit Mitternacht als Start und Ende des
  // Folgetags. Ohne diese Erkennung wuerde das Formular beim Speichern zwei
  // zusaetzliche Stunden anhaengen.
  const start = toDateTimeLocal(termin.start_time);
  const ende = toDateTimeLocal(termin.end_time);
  const ganztag = termin.all_day || ende.slice(0, 10) !== start.slice(0, 10);
  return {
    titel: termin.title,
    beschreibung: termin.description ?? "",
    ganztag: termin.all_day,
    tag: toDateInput(termin.start_time),
    von: start.slice(11, 16),
    bis: ganztag ? start.slice(11, 16) : ende.slice(11, 16),
  };
}

function alsEingabe(formular: KalenderFormular): CalendarEventWrite | { fehler: string } {
  if (!formular.titel.trim()) {
    return { fehler: "Gib dem Termin einen Titel." };
  }
  if (!formular.tag) {
    return { fehler: "An welchem Tag findet der Termin statt?" };
  }
  if (formular.ganztag) {
    // Ganztag: Ende ist der **Start des Folgetags**. Das Backend lehnt
    // `end_time < start_time` mit einem 422 ab, und genau daran scheitert eine
    //-nahe" Terminanlage, wenn beide auf denselben Tag fallen.
    const folgetag = toUtcIsoFromParts(naechsterTag(formular.tag), "00:00");
    const start = toUtcIsoFromParts(formular.tag, "00:00");
    if (!start || !folgetag) {
      return { fehler: "Dieses Datum lässt sich nicht lesen. Bitte erneut wählen." };
    }
    return {
      title: formular.titel.trim(),
      start_time: start,
      end_time: folgetag,
      description: formular.beschreibung.trim() || null,
      all_day: true,
    };
  }
  if (!formular.von || !formular.bis) {
    return { fehler: "Von wann bis wann geht der Termin?" };
  }
  const start = toUtcIsoFromParts(formular.tag, formular.von);
  const ende = toUtcIsoFromParts(formular.tag, formular.bis);
  if (!start || !ende) {
    return { fehler: "Diese Uhrzeit lässt sich nicht lesen. Bitte erneut wählen." };
  }
  if (ende <= start) {
    // Der Client faengt es ab, damit der Lernende eine verstaendliche
    // Meldung bekommt statt "end_time must not precede start_time".
    return { fehler: "Das Ende liegt vor dem Beginn. Bitte die Zeiten prüfen." };
  }
  return {
    title: formular.titel.trim(),
    start_time: start,
    end_time: ende,
    description: formular.beschreibung.trim() || null,
    all_day: false,
  };
}

function naechsterTag(tag: string) {
  const datum = new Date(`${tag}T12:00:00`);
  datum.setDate(datum.getDate() + 1);
  return toIsoDate(datum);
}

function alsAenderung(formular: KalenderFormular): CalendarEventChanges {
  const eingabe = alsEingabe(formular);
  if ("fehler" in eingabe) {
    return {};
  }
  const { title, start_time, end_time, description, all_day } = eingabe;
  return { title, start_time, end_time, description, all_day };
}

export function CalendarSection({ reference }: { reference: Date }) {
  const sammlung = useCollection<CalendarEvent, CalendarEventWrite, CalendarEventChanges>(
    () => getCalendarEvents(false),
    { create: createCalendarEvent, update: updateCalendarEvent, remove: deleteCalendarEvent },
    { sort: (a, b) => a.start_time.localeCompare(b.start_time) || a.id - b.id },
  );
  const [formularOffen, setFormularOffen] = useState(false);
  const [bearbeitet, setBearbeitet] = useState<number | null>(null);
  const [formular, setFormular] = useState<KalenderFormular>(leeresFormular);
  const [formularFehler, setFormularFehler] = useState<string | null>(null);

  const istSpeichern = sammlung.pending === "create" || sammlung.pending === `update:${bearbeitet}`;

  function oeffnen(termin?: CalendarEvent) {
    setBearbeitet(termin?.id ?? null);
    setFormular(termin ? ausTermin(termin) : leeresFormular);
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
    const eingabe = alsEingabe(formular);
    if ("fehler" in eingabe) {
      setFormularFehler(eingabe.fehler);
      return;
    }
    setFormularFehler(null);
    const ergebnis = bearbeitet === null ? await sammlung.create(eingabe) : await sammlung.update(bearbeitet, alsAenderung(formular));
    if (ergebnis) {
      schliessen();
    }
  }

  // Nach Tag gruppieren, damit ein Tag mit drei Terminen nicht drei
  // Ueberschriften bekommt.
  const nachTag = sammlung.items.reduce<Map<string, CalendarEvent[]>>((gruppen, termin) => {
    const schluessel = toIsoDate(termin.start_time);
    const vorhandene = gruppen.get(schluessel);
    if (vorhandene) {
      vorhandene.push(termin);
    } else {
      gruppen.set(schluessel, [termin]);
    }
    return gruppen;
  }, new Map());

  return (
    <Card id="calendar" className={anchorClass}>
      <CardHeader className="flex-row flex-wrap items-start justify-between space-y-0 pb-4">
        <div>
          <CardTitle className="flex items-center gap-2 text-lg">
            <CalendarDays className="h-5 w-5 text-primary" aria-hidden="true" />
            Kalender
          </CardTitle>
          <CardDescription className="mt-1">Termine und Erinnerungen für deinen Lernalltag.</CardDescription>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant="secondary">{sammlung.items.length} Termine</Badge>
          <Button type="button" size="icon" variant="ghost" onClick={() => void sammlung.reload()} aria-label="Kalender neu laden" disabled={sammlung.status === "loading"}>
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
              <p className="text-sm font-bold">{bearbeitet === null ? "Neuer Termin" : "Termin bearbeiten"}</p>
              <Button type="button" size="icon" variant="ghost" onClick={schliessen} disabled={istSpeichern} aria-label="Formular schließen">
                <X aria-hidden="true" />
              </Button>
            </div>

            <Field id="termin-titel" label="Titel" required>
              {(props) => (
                <Input
                  {...props}
                  value={formular.titel}
                  onChange={(event) => setFormular((current) => ({ ...current, titel: event.target.value }))}
                  disabled={istSpeichern}
                  placeholder="Zum Beispiel: Elternabend"
                />
              )}
            </Field>

            <div className="grid gap-4 sm:grid-cols-3">
              <Field id="termin-tag" label="Tag" required>
                {(props) => (
                  <Input
                    {...props}
                    type="date"
                    value={formular.tag}
                    onChange={(event) => setFormular((current) => ({ ...current, tag: event.target.value }))}
                    disabled={istSpeichern}
                  />
                )}
              </Field>

              <Field id="termin-von" label="Von" required={!formular.ganztag}>
                {(props) => (
                  <Input
                    {...props}
                    type="time"
                    value={formular.von}
                    onChange={(event) => setFormular((current) => ({ ...current, von: event.target.value }))}
                    disabled={istSpeichern || formular.ganztag}
                  />
                )}
              </Field>

              <Field id="termin-bis" label="Bis" required={!formular.ganztag}>
                {(props) => (
                  <Input
                    {...props}
                    type="time"
                    value={formular.bis}
                    onChange={(event) => setFormular((current) => ({ ...current, bis: event.target.value }))}
                    disabled={istSpeichern || formular.ganztag}
                  />
                )}
              </Field>
            </div>

            <label className="flex items-center gap-2.5 text-sm font-semibold">
              <input
                type="checkbox"
                checked={formular.ganztag}
                onChange={(event) => setFormular((current) => ({ ...current, ganztag: event.target.checked }))}
                disabled={istSpeichern}
                className="h-4 w-4 border-input bg-card accent-[var(--foreground)]"
              />
              Ganztägig
            </label>

            <Field id="termin-beschreibung" label="Notiz">
              {(props) => (
                <Textarea
                  {...props}
                  value={formular.beschreibung}
                  onChange={(event) => setFormular((current) => ({ ...current, beschreibung: event.target.value }))}
                  disabled={istSpeichern}
                  placeholder="Aula, Eingang Nord"
                />
              )}
            </Field>

            <FormError message={formularFehler} />

            <div className="flex flex-wrap items-center gap-2">
              <Button type="submit" disabled={istSpeichern}>
                {istSpeichern ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <CheckCircle2 aria-hidden="true" />}
                {istSpeichern ? "Wird gespeichert …" : bearbeitet === null ? "Termin anlegen" : "Änderungen speichern"}
              </Button>
              <Button type="button" variant="ghost" onClick={schliessen} disabled={istSpeichern}>
                Abbrechen
              </Button>
            </div>
          </form>
        ) : null}

        {sammlung.status === "loading" && sammlung.items.length === 0 ? (
          <div className="space-y-3" aria-busy="true" aria-label="Kalender wird geladen">
            <Skeleton className="h-20 w-full" />
            <Skeleton className="h-20 w-full" />
          </div>
        ) : sammlung.items.length === 0 ? (
          <EmptyState icon={CalendarDays} title="Keine Termine" description="Dein Kalender ist frei. Trag einen Termin ein, damit er nicht untergeht." />
        ) : (
          <div className="space-y-5">
            {[...nachTag.entries()].map(([tag, termine]) => (
              <section key={tag} aria-label={formatDate(tag, { weekday: "long", day: "2-digit", month: "long" })}>
                <h4 className={cn("mb-2 text-[11px] font-bold uppercase tracking-[0.16em] text-muted-foreground", isSameDay(tag, reference) && "text-primary")}>
                  {isSameDay(tag, reference) ? "Heute" : formatDate(tag, { weekday: "long", day: "2-digit", month: "long" })}
                </h4>
                <ul className="space-y-2.5">
                  {termine.map((termin) => (
                    <TerminZeile
                      key={termin.id}
                      termin={termin}
                      busy={sammlung.pending !== null}
                      onEdit={() => oeffnen(termin)}
                      onDelete={() => sammlung.remove(termin.id)}
                    />
                  ))}
                </ul>
              </section>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function TerminZeile({ termin, busy, onEdit, onDelete }: { termin: CalendarEvent; busy: boolean; onEdit: () => void; onDelete: () => void }) {
  return (
    <li className="flex items-start gap-3 border border-border bg-card/60 p-3.5 transition-colors hover:border-foreground/40">
      <div className="flex h-12 w-12 shrink-0 flex-col items-center justify-center bg-secondary text-secondary-foreground">
        <span className="text-[11px] font-bold uppercase">{formatDate(termin.start_time, { month: "short" })}</span>
        <span className="text-lg font-extrabold leading-5">{formatDate(termin.start_time, { day: "2-digit" })}</span>
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-extrabold">{termin.title}</p>
        <p className="mt-1 flex items-center gap-1.5 text-xs font-semibold text-muted-foreground">
          <Clock3 className="h-3.5 w-3.5" aria-hidden="true" />
          {termin.all_day ? "Ganztägig" : `${formatTime(termin.start_time)} – ${formatTime(termin.end_time)}`}
        </p>
        {termin.description ? <p className="mt-1 line-clamp-2 text-xs leading-5 text-muted-foreground">{termin.description}</p> : null}
      </div>
      <RowActions label={termin.title} onEdit={onEdit} onDelete={onDelete} busy={busy} />
    </li>
  );
}
