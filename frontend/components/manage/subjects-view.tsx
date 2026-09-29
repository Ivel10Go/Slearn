"use client";

import { useState, type FormEvent } from "react";
import { BookOpen, CheckCircle2, LoaderCircle, Plus, RefreshCw, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Field, FormError } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { RowActions, SectionBanner } from "@/components/manage/row-actions";
import { createSubject, deleteSubject, getSubjects, updateSubject, type SubjectChanges, type SubjectWrite } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { useCollection } from "@/lib/use-collection";
import { cn } from "@/lib/utils";
import type { Subject } from "@/lib/types";

interface FachFormular {
  name: string;
  farbe: string;
  beschreibung: string;
}

const leeresFormular: FachFormular = { name: "", farbe: "#3B82F6", beschreibung: "" };

/**
 * Das Backend validiert die Farbe selbst: 4 bis 20 Zeichen und ein
 * `#rrggbb`- Muster. Der gleiche Test hier ist nur dafuer da, damit die
 * Meldung deutsch ist und die Abschicktaste nicht umsonst einen 422 erzeugt.
 */
const FARBE_MUSTER = /^#[0-9a-fA-F]{6}$/;

function ausFach(fach: Subject): FachFormular {
  return { name: fach.name, farbe: fach.color, beschreibung: fach.description ?? "" };
}

function alsEingabe(formular: FachFormular): SubjectWrite {
  return { name: formular.name.trim(), color: formular.farbe, description: formular.beschreibung.trim() || null };
}

function alsAenderung(formular: FachFormular): SubjectChanges {
  return { name: formular.name.trim(), color: formular.farbe, description: formular.beschreibung.trim() || null };
}

export function SubjectsView() {
  const sammlung = useCollection<Subject, SubjectWrite, SubjectChanges>(() => getSubjects(), {
    create: createSubject,
    update: updateSubject,
    remove: deleteSubject,
  });
  const [formularOffen, setFormularOffen] = useState(false);
  const [bearbeitet, setBearbeitet] = useState<number | null>(null);
  const [formular, setFormular] = useState<FachFormular>(leeresFormular);
  const [formularFehler, setFormularFehler] = useState<string | null>(null);

  const istSpeichern = sammlung.pending === "create" || sammlung.pending === `update:${bearbeitet}`;

  function oeffnen(fach?: Subject) {
    setBearbeitet(fach?.id ?? null);
    setFormular(fach ? ausFach(fach) : leeresFormular);
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
    if (!formular.name.trim()) {
      setFormularFehler("Gib dem Fach einen Namen.");
      return;
    }
    if (!FARBE_MUSTER.test(formular.farbe.trim())) {
      setFormularFehler("Die Farbe muss als sechsstelliger Hex-Wert stehen, etwa #3B82F6.");
      return;
    }
    setFormularFehler(null);
    const eingabe = { ...alsEingabe(formular), color: formular.farbe.trim() };
    const ergebnis = bearbeitet === null ? await sammlung.create(eingabe) : await sammlung.update(bearbeitet, { ...alsAenderung(formular), color: formular.farbe.trim() });
    if (ergebnis) {
      schliessen();
    }
  }

  return (
    <div className="space-y-6">
      <header>
        <p className="mb-2 text-sm font-bold uppercase tracking-[0.18em] text-primary">Dein Lernraum</p>
        <h1 className="text-3xl font-extrabold tracking-[-0.035em] sm:text-4xl">Fächer</h1>
        <p className="mt-2 text-sm font-semibold text-muted-foreground">
          Jede Hausaufgabe, Prüfung und Lernaufgabe kann einem Fach zugeordnet werden.
        </p>
      </header>

      <Card className="border-border/80 bg-card/90">
        <CardHeader className="flex-row flex-wrap items-start justify-between gap-3 space-y-0 pb-4">
          <div>
            <CardTitle className="flex items-center gap-2">
              <BookOpen className="h-5 w-5 text-primary" aria-hidden="true" />
              Meine Fächer
            </CardTitle>
            <CardDescription className="mt-1">Von Hand angelegt oder aus dem Schulportal importiert.</CardDescription>
          </div>
          <div className="flex items-center gap-2">
            <Badge variant="secondary">{sammlung.items.length} Fächer</Badge>
            <Button type="button" size="icon" variant="ghost" onClick={() => void sammlung.reload()} aria-label="Fächer neu laden" disabled={sammlung.status === "loading"}>
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
                <p className="text-sm font-bold">{bearbeitet === null ? "Neues Fach" : "Fach bearbeiten"}</p>
                <Button type="button" size="icon" variant="ghost" onClick={schliessen} disabled={istSpeichern} aria-label="Formular schließen">
                  <X aria-hidden="true" />
                </Button>
              </div>

              <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_9rem]">
                <Field id="fach-name" label="Name" required>
                  {(props) => (
                    <Input
                      {...props}
                      value={formular.name}
                      onChange={(event) => setFormular((current) => ({ ...current, name: event.target.value }))}
                      disabled={istSpeichern}
                      placeholder="Zum Beispiel: Astronomie"
                    />
                  )}
                </Field>

                <Field
                  id="fach-farbe"
                  label="Farbe"
                  required
                  hint="Als Hex-Wert."
                >
                  {(props) => (
                    <Input
                      {...props}
                      value={formular.farbe}
                      onChange={(event) => setFormular((current) => ({ ...current, farbe: event.target.value }))}
                      disabled={istSpeichern}
                      placeholder="#3B82F6"
                      spellCheck={false}
                      className="font-mono"
                    />
                  )}
                </Field>
              </div>

              <Field id="fach-beschreibung" label="Beschreibung" hint="Optional. Zum Beispiel die Lehrkraft oder der Raum.">
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
                  {istSpeichern ? "Wird gespeichert …" : bearbeitet === null ? "Fach anlegen" : "Änderungen speichern"}
                </Button>
                <Button type="button" variant="ghost" onClick={schliessen} disabled={istSpeichern}>
                  Abbrechen
                </Button>
              </div>
            </form>
          ) : null}

          {sammlung.status === "loading" && sammlung.items.length === 0 ? (
            <div className="space-y-3" aria-busy="true" aria-label="Fächer werden geladen">
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-16 w-full" />
            </div>
          ) : sammlung.items.length === 0 ? (
            <EmptyState icon={BookOpen} title="Noch keine Fächer" description="Lege deine Fächer an, damit Aufgaben und Prüfungen sortiert werden." />
          ) : (
            <ul className="space-y-2.5">
              {sammlung.items.map((fach) => (
                <li key={fach.id} className="flex items-start gap-3 border border-border bg-card/60 p-3.5 transition-colors hover:border-foreground/40">
                  {/*
                    Die Farbe erscheint hier als kleiner Farbwert, nicht als
                    eingefärbte Kachel. Das Design ist durchgehend monochrom -
                    ein bunter Fleck je Zeile wuerde der einzige Ort sein, an
                    dem Farbe etwas bedeutet, ohne erklaert zu sein. Der Wert
                    steht als Text daneben, damit er pruefbar bleibt.
                  */}
                  <span
                    aria-hidden="true"
                    className="mt-1 h-5 w-5 shrink-0 border border-border"
                    style={{ backgroundColor: fach.color }}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="truncate text-sm font-extrabold">{fach.name}</p>
                      {fach.source !== "manual" ? <Badge variant="outline">aus dem Portal</Badge> : null}
                    </div>
                    {fach.description ? <p className="mt-1 line-clamp-2 text-xs leading-5 text-muted-foreground">{fach.description}</p> : null}
                    <p className="mt-1.5 text-xs font-semibold text-muted-foreground">
                      <span className="font-mono">{fach.color}</span>
                      <span className="text-border"> · </span>
                      angelegt {formatDate(fach.created_at, { day: "2-digit", month: "short", year: "numeric" })}
                    </p>
                  </div>
                  <RowActions
                    label={fach.name}
                    onEdit={() => oeffnen(fach)}
                    onDelete={() => sammlung.remove(fach.id)}
                    busy={sammlung.pending !== null}
                  />
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
