"use client";

import { useState, type FormEvent } from "react";
import { CheckCircle2, LoaderCircle, Plus, RefreshCw, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Field, FormError } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { RowActions, SectionBanner } from "@/components/manage/row-actions";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { StudyPlan } from "@/lib/types";

const STATUS_LABEL: Record<StudyPlan["status"], string> = {
  active: "Aktiv",
  paused: "Pausiert",
  finished: "Abgeschlossen",
};

/** `renameStudyPlan` aendert nur den Titel - mehr gibt es am Plan nicht zu aendern. */
export interface PlanProps {
  plans: StudyPlan[];
  status: "loading" | "ready" | "error";
  error: string | null;
  actionError: string | null;
  pending: string | null;
  onReload: () => void;
  onCreate: (input: { title: string; description: string | null }) => Promise<unknown>;
  onRename: (planId: number, titel: string) => void;
  onDelete: (planId: number) => void;
}

/**
 * Die Karte erhaelt ihre Daten von oben statt selbst zu laden.
 *
 * Serien und Aufgaben brauchen dieselbe Planliste, um ihre Auswahlfelder zu
 * fuellen. Laede hier eine eigene, waere das eine zweite Anfrage - und nach
 * dem Anlegen eines Plans wuessten die beiden anderen Karten den neuen Plan
 * erst nach einem Neuladen.
 */
export function StudyPlansCard({ plans, status, error, actionError, pending, onReload, onCreate, onRename, onDelete }: PlanProps) {
  const [formularOffen, setFormularOffen] = useState(false);
  const [formular, setFormular] = useState({ titel: "", beschreibung: "" });
  const [formularFehler, setFormularFehler] = useState<string | null>(null);

  const istSpeichern = pending === "create";

  async function absenden(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!formular.titel.trim()) {
      setFormularFehler("Gib dem Lernplan einen Titel.");
      return;
    }
    setFormularFehler(null);
    const ergebnis = await onCreate({ title: formular.titel.trim(), description: formular.beschreibung.trim() || null });
    if (ergebnis) {
      setFormular({ titel: "", beschreibung: "" });
      setFormularOffen(false);
    }
  }

  return (
    <Card className="border-border/80 bg-card/90">
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-3 space-y-0 pb-3">
        <div>
          <CardTitle>Lernpläne</CardTitle>
          <CardDescription className="mt-1">Jeder Plan sammelt Aufgaben zu einem Thema. Ohne Plan gibt es keine Aufgaben.</CardDescription>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant="secondary">{plans.length} Pläne</Badge>
          <Button type="button" size="icon" variant="ghost" onClick={onReload} aria-label="Lernpläne neu laden" disabled={status === "loading"}>
            <RefreshCw className={cn(status === "loading" && "animate-spin")} aria-hidden="true" />
          </Button>
          <Button type="button" size="sm" onClick={() => setFormularOffen((offen) => !offen)} aria-expanded={formularOffen}>
            {formularOffen ? <X aria-hidden="true" /> : <Plus aria-hidden="true" />}
            {formularOffen ? "Schließen" : "Neu"}
          </Button>
        </div>
      </CardHeader>

      <CardContent className="space-y-4">
        <SectionBanner error={error} actionError={actionError} onRetry={onReload} />

        {formularOffen ? (
          <form onSubmit={absenden} noValidate className="space-y-4 rounded-md border border-primary/20 bg-primary/5 p-4">
            <Field id="plan-titel" label="Titel" required>
              {(props) => (
                <Input
                  {...props}
                  value={formular.titel}
                  onChange={(event) => setFormular((current) => ({ ...current, titel: event.target.value }))}
                  disabled={istSpeichern}
                  placeholder="Zum Beispiel: Abi-Vorbereitung Mathematik"
                />
              )}
            </Field>
            <Field id="plan-beschreibung" label="Beschreibung" hint="Optional.">
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
                {istSpeichern ? "Wird gespeichert …" : "Lernplan anlegen"}
              </Button>
              <Button type="button" variant="ghost" onClick={() => setFormularOffen(false)} disabled={istSpeichern}>
                Abbrechen
              </Button>
            </div>
          </form>
        ) : null}

        {status === "loading" && plans.length === 0 ? (
          <div className="space-y-3" aria-busy="true" aria-label="Lernpläne werden geladen">
            <Skeleton className="h-16 w-full" />
          </div>
        ) : plans.length === 0 ? (
          <EmptyState
            icon={CheckCircle2}
            title="Noch kein Lernplan"
            description="Lege einen Lernplan an – darin liegen danach alle Lernaufgaben, Serien und Prüfungsvorbereitungen."
          />
        ) : (
          <ul className="space-y-2.5">
            {plans.map((plan) => (
              <PlanZeile
                key={plan.id}
                plan={plan}
                busy={pending !== null}
                onRename={(titel) => onRename(plan.id, titel)}
                onDelete={() => onDelete(plan.id)}
              />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function PlanZeile({ plan, busy, onRename, onDelete }: { plan: StudyPlan; busy: boolean; onRename: (titel: string) => void; onDelete: () => void }) {
  const [umbenennen, setUmbenennen] = useState(false);
  const [titel, setTitel] = useState(plan.title);

  function speichern(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const neuerTitel = titel.trim();
    // Unveraendert heisst hier: zurueck. `update` wuerde trotzdem einen
    // PATCH schicken und den `updated_at`-Stempel verschieben.
    if (neuerTitel && neuerTitel !== plan.title) {
      onRename(neuerTitel);
    }
    setUmbenennen(false);
  }

  return (
    <li className="flex items-start gap-3 border border-border bg-card/60 p-3.5 transition-colors hover:border-foreground/40">
      <div className="min-w-0 flex-1">
        {umbenennen ? (
          <form onSubmit={speichern} className="flex flex-wrap items-center gap-2">
            <label htmlFor={`plan-umbenennen-${plan.id}`} className="sr-only">
              Neuer Titel für {plan.title}
            </label>
            <Input
              id={`plan-umbenennen-${plan.id}`}
              value={titel}
              onChange={(event) => setTitel(event.target.value)}
              autoFocus
              className="h-9 min-w-0 flex-1"
            />
            <Button type="submit" size="sm" disabled={busy}>
              Speichern
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => {
                setTitel(plan.title);
                setUmbenennen(false);
              }}
            >
              Abbrechen
            </Button>
          </form>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <p className="truncate text-sm font-semibold">{plan.title}</p>
              <Badge variant={plan.status === "active" ? "success" : "outline"}>{STATUS_LABEL[plan.status]}</Badge>
            </div>
            {plan.description ? <p className="mt-1 line-clamp-2 text-xs leading-5 text-muted-foreground">{plan.description}</p> : null}
            <p className="mt-1.5 text-xs font-semibold text-muted-foreground">
              {plan.open_task_count} von {plan.task_count} Aufgaben offen
              {plan.starts_on ? (
                <>
                  <span className="text-border"> · </span>
                  ab {formatDate(plan.starts_on, { day: "2-digit", month: "short", year: "numeric" })}
                </>
              ) : null}
            </p>
          </>
        )}
      </div>

      {!umbenennen ? (
        <RowActions
          label={plan.title}
          onEdit={() => {
            setTitel(plan.title);
            setUmbenennen(true);
          }}
          onDelete={onDelete}
          busy={busy}
        />
      ) : null}
    </li>
  );
}
