"use client";

import { useEffect, useState } from "react";
import { LoaderCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { FormError } from "@/components/ui/field";
import {
  bundleLessonChoices,
  getLessonChoices,
  saveLessonChoices,
  type LessonChoiceBundle,
  type LessonChoiceWindow,
} from "@/lib/lesson-choices";
import { cn } from "@/lib/utils";

const WOCHENTAGE = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];

interface LessonChoicesCardProps {
  /** Nach dem Speichern: Faecher und Stunden neu laden. */
  onSaved: () => void;
}

function fehlertext(error: unknown): string {
  return error instanceof Error ? error.message : "Das hat nicht geklappt.";
}

function zeitfenster(fenster: LessonChoiceWindow): string {
  return `${WOCHENTAGE[fenster.weekday] ?? "?"} ${fenster.start}–${fenster.end}`;
}

/**
 * Parallele Stunden: mehrere Faecher liegen im Plan zur selben Zeit, besucht
 * wird eines davon (Wahlfach, Kurs). Der Server liefert je Stunde eine Gruppe;
 * die Karte fasst alle Gruppen mit denselben Faechern zu einer Frage zusammen
 * („NBT oder Spanisch?“) - eine Doppelstunde oder derselbe Kurs an zwei Tagen
 * ist dieselbe Entscheidung. Ein Klick speichert sofort fuer alle diese Stunden;
 * die abgewaehlten fallen aus dem Plan und kommen beim naechsten Abruf nicht zurueck.
 */
export function LessonChoicesCard({ onSaved }: LessonChoicesCardProps) {
  const [buendel, setBuendel] = useState<LessonChoiceBundle[]>([]);
  const [speichert, setSpeichert] = useState<string | null>(null);
  const [fehler, setFehler] = useState<string | null>(null);
  const [hinweis, setHinweis] = useState<string | null>(null);

  useEffect(() => {
    let abgebrochen = false;
    void getLessonChoices()
      .then((geladen) => {
        if (!abgebrochen) setBuendel(bundleLessonChoices(geladen));
      })
      // Ohne Parallelstunden ist die Karte nur nicht da; ein Ladefehler ist hier kein Grund, die Seite zu stoeren.
      .catch(() => undefined);
    return () => {
      abgebrochen = true;
    };
  }, []);

  if (buendel.length === 0) return null;

  const offen = buendel.filter((eintrag) => !eintrag.decided).length;

  async function waehlen(eintrag: LessonChoiceBundle, fachId: number) {
    if (eintrag.decided && eintrag.selected.length === 1 && eintrag.selected[0] === fachId) return;
    setSpeichert(eintrag.id);
    setFehler(null);
    setHinweis(null);
    try {
      const ergebnis = await saveLessonChoices(Object.fromEntries(eintrag.keys.map((key) => [key, [fachId]])));
      setBuendel(bundleLessonChoices(ergebnis.groups));
      if (ergebnis.resynced) setHinweis("Gespeichert. Der Stundenplan wurde neu abgerufen.");
      onSaved();
    } catch (error) {
      setFehler(fehlertext(error));
    } finally {
      setSpeichert(null);
    }
  }

  return (
    <Card className="border-border/80 bg-card/90">
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-3 space-y-0 pb-3">
        <div>
          <CardTitle>Parallele Stunden</CardTitle>
          <CardDescription className="mt-1">
            Diese Fächer liegen im Plan zur selben Zeit. Wähle einmal, was du besuchst – das gilt für alle diese Stunden.
          </CardDescription>
        </div>
        {offen > 0 ? <Badge variant="secondary">{offen} offen</Badge> : null}
      </CardHeader>
      <CardContent className="space-y-3">
        <ul className="divide-y divide-border border-y border-border">
          {buendel.map((eintrag) => {
            const titel = eintrag.options.map((option) => option.name).join(" oder ");
            return (
              <li key={eintrag.id} className="flex flex-col gap-2 py-3 sm:flex-row sm:items-center sm:justify-between sm:gap-4">
                <div className="min-w-0">
                  <p className="text-[13px] font-medium">
                    {titel}
                    {eintrag.decided ? null : <span className="ml-2 text-xs font-normal text-muted-foreground">noch offen</span>}
                  </p>
                  <p className="mt-0.5 text-xs text-muted-foreground">{eintrag.windows.map(zeitfenster).join(" · ")}</p>
                </div>
                <div className="flex items-center gap-2">
                  {speichert === eintrag.id ? (
                    <LoaderCircle className="size-4 animate-spin text-muted-foreground" aria-label="Wird gespeichert" />
                  ) : null}
                  <div role="group" aria-label={`Besuchtes Fach: ${titel}`} className="inline-flex rounded-md border border-border p-0.5">
                    {eintrag.options.map((option) => {
                      const aktiv = eintrag.selected.includes(option.subject_id);
                      return (
                        <button
                          key={option.subject_id}
                          type="button"
                          aria-pressed={aktiv}
                          disabled={speichert !== null}
                          onClick={() => void waehlen(eintrag, option.subject_id)}
                          className={cn(
                            "h-7 rounded-[4px] px-3 text-[13px] font-medium transition-colors disabled:opacity-60",
                            aktiv ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted hover:text-foreground",
                          )}
                        >
                          <span className="inline-flex items-center gap-1.5">
                            {option.color ? (
                              <span className="size-2 shrink-0 rounded-full" style={{ backgroundColor: option.color }} aria-hidden="true" />
                            ) : null}
                            {option.name}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                </div>
              </li>
            );
          })}
        </ul>

        <FormError message={fehler} />
        {hinweis ? (
          <p className="text-[13px] text-muted-foreground" role="status">
            {hinweis}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
