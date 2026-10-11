"use client";

import { useState, type FormEvent } from "react";
import { CalendarDays, CheckCircle2, Clock3, LoaderCircle, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Field, FormError } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { RowActions, SectionBanner, SectionHeader } from "@/components/manage/row-actions";
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
import { t, tn } from "@/lib/i18n";

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
    return { fehler: t("Gib dem Termin einen Titel.") };
  }
  if (!formular.tag) {
    return { fehler: t("An welchem Tag findet der Termin statt?") };
  }
  if (formular.ganztag) {
    // Ganztag: Ende ist der **Start des Folgetags**. Das Backend lehnt
    // `end_time < start_time` mit einem 422 ab, und genau daran scheitert eine
    //-nahe" Terminanlage, wenn beide auf denselben Tag fallen.
    const folgetag = toUtcIsoFromParts(naechsterTag(formular.tag), "00:00");
    const start = toUtcIsoFromParts(formular.tag, "00:00");
    if (!start || !folgetag) {
      return { fehler: t("Dieses Datum lässt sich nicht lesen. Bitte erneut wählen.") };
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
    return { fehler: t("Von wann bis wann geht der Termin?") };
  }
  const start = toUtcIsoFromParts(formular.tag, formular.von);
  const ende = toUtcIsoFromParts(formular.tag, formular.bis);
  if (!start || !ende) {
    return { fehler: t("Diese Uhrzeit lässt sich nicht lesen. Bitte erneut wählen.") };
  }
  if (ende <= start) {
    // Der Client faengt es ab, damit der Lernende eine verstaendliche
    // Meldung bekommt statt "end_time must not precede start_time".
    return { fehler: t("Das Ende liegt vor dem Beginn. Bitte die Zeiten prüfen.") };
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

  return (
    <Card id="calendar" className={cn(anchorClass, "@container")}>
      <SectionHeader
        title={t("Termine")}
        count={tn(sammlung.items.length, "{n} Termin", "{n} Termine")}
        loading={sammlung.status === "loading"}
        onReload={() => void sammlung.reload()}
        formOpen={formularOffen && bearbeitet === null}
        onToggleForm={() => (formularOffen && bearbeitet === null ? schliessen() : oeffnen())}
        newLabel={t("Neuer Termin")}
      />

      <CardContent className="space-y-3 pb-0">
        <SectionBanner error={sammlung.error} actionError={sammlung.actionError} onRetry={() => void sammlung.reload()} />

        {formularOffen ? (
          <form onSubmit={absenden} noValidate className="space-y-4 rounded-md border border-border bg-muted/40 p-4">
            <div className="flex items-start justify-between gap-3">
              <p className="text-sm font-semibold">{bearbeitet === null ? t("Neuer Termin") : t("Termin bearbeiten")}</p>
              <Button type="button" size="icon" variant="ghost" onClick={schliessen} disabled={istSpeichern} aria-label={t("Formular schließen")}>
                <X aria-hidden="true" />
              </Button>
            </div>

            <Field id="termin-titel" label={t("Titel")} required>
              {(props) => (
                <Input
                  {...props}
                  value={formular.titel}
                  onChange={(event) => setFormular((current) => ({ ...current, titel: event.target.value }))}
                  disabled={istSpeichern}
                  placeholder={t("Zum Beispiel: Elternabend")}
                />
              )}
            </Field>

            <div className="grid grid-cols-2 gap-4 @md:grid-cols-3">
              {/* Im schmalen Raster steht der Tag allein in der ersten Zeile,
                  Von und Bis teilen sich die zweite. */}
              <Field id="termin-tag" label={t("Tag")} required className="col-span-2 @md:col-span-1">
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

              <Field id="termin-von" label={t("Von")} required={!formular.ganztag}>
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

              <Field id="termin-bis" label={t("Bis")} required={!formular.ganztag}>
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
              {t("Ganztägig")}
            </label>

            <Field id="termin-beschreibung" label={t("Notiz")}>
              {(props) => (
                <Textarea
                  {...props}
                  value={formular.beschreibung}
                  onChange={(event) => setFormular((current) => ({ ...current, beschreibung: event.target.value }))}
                  disabled={istSpeichern}
                  placeholder={t("Aula, Eingang Nord")}
                />
              )}
            </Field>

            <FormError message={formularFehler} />

            <div className="flex flex-wrap items-center gap-2">
              <Button type="submit" disabled={istSpeichern}>
                {istSpeichern ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <CheckCircle2 aria-hidden="true" />}
                {istSpeichern ? t("Wird gespeichert …") : bearbeitet === null ? t("Termin anlegen") : t("Änderungen speichern")}
              </Button>
              <Button type="button" variant="ghost" onClick={schliessen} disabled={istSpeichern}>
                {t("Abbrechen")}
              </Button>
            </div>
          </form>
        ) : null}

        {sammlung.status === "loading" && sammlung.items.length === 0 ? (
          <div className="space-y-2 pb-4" aria-busy="true" aria-label={t("Termine werden geladen")}>
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : sammlung.items.length === 0 ? (
          <div className="pb-4">
            <EmptyState icon={CalendarDays} title={t("Keine Termine")} description={t("Dein Kalender ist frei.")} />
          </div>
        ) : (
          // Eine flache Liste statt Tagesgruppen: das Kalenderblatt vorn zeigt
          // den Tag schon, eine Ueberschrift je Tag sagte ihn ein zweites Mal.
          <ul className="-mx-4 divide-y divide-border border-t border-border">
            {sammlung.items.map((termin) => (
              <TerminZeile
                key={termin.id}
                termin={termin}
                heute={isSameDay(termin.start_time, reference)}
                busy={sammlung.pending !== null}
                onEdit={() => oeffnen(termin)}
                onDelete={() => sammlung.remove(termin.id)}
              />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function TerminZeile({
  termin,
  heute,
  busy,
  onEdit,
  onDelete,
}: {
  termin: CalendarEvent;
  heute: boolean;
  busy: boolean;
  onEdit: () => void;
  onDelete: () => void;
}) {
  return (
    <li className="flex items-start gap-3 px-4 py-2.5">
      <div
        className={cn(
          "flex h-10 w-10 shrink-0 flex-col items-center justify-center rounded-md border border-border",
          heute && "border-foreground bg-foreground text-background",
        )}
        aria-hidden="true"
      >
        <span className={cn("text-[10px] leading-3", heute ? "text-background/70" : "text-muted-foreground")}>
          {formatDate(termin.start_time, { month: "short" })}
        </span>
        <span className="text-[15px] font-semibold leading-5 tabular-nums">{formatDate(termin.start_time, { day: "2-digit" })}</span>
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-[13px] font-medium">{termin.title}</p>
        <p className="mt-0.5 flex items-center gap-1 text-xs text-muted-foreground">
          <Clock3 className="h-3 w-3" aria-hidden="true" />
          <span className="sr-only">{formatDate(termin.start_time, { weekday: "long", day: "2-digit", month: "long" })},</span>
          {heute ? t("Heute · ") : null}
          {termin.all_day ? t("Ganztägig") : `${formatTime(termin.start_time)} – ${formatTime(termin.end_time)}`}
        </p>
        {termin.description ? <p className="mt-0.5 line-clamp-1 text-xs leading-5 text-muted-foreground">{termin.description}</p> : null}
      </div>
      <RowActions label={termin.title} onEdit={onEdit} onDelete={onDelete} busy={busy} />
    </li>
  );
}
