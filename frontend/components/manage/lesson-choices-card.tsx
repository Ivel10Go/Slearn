"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, LoaderCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { FormError } from "@/components/ui/field";
import { getLessonChoices, saveLessonChoices, type LessonChoiceGroup } from "@/lib/lesson-choices";

const WOCHENTAGE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"];

interface LessonChoicesCardProps {
  /** Nach dem Speichern: Faecher und Stunden neu laden. */
  onSaved: () => void;
}

function fehlertext(error: unknown): string {
  return error instanceof Error ? error.message : "Das hat nicht geklappt.";
}

function auswahlAus(groups: LessonChoiceGroup[]): Record<string, number[]> {
  return Object.fromEntries(groups.map((group) => [group.key, group.options.filter((o) => o.selected).map((o) => o.subject_id)]));
}

/**
 * Parallele Stunden: mehrere Faecher liegen im Plan zur selben Zeit, besucht
 * wird davon nur ein Teil (Wahlfach, Kurs). Die Karte erscheint nur, wenn es
 * solche Zeitfenster gibt. Gespeichert wird je Zeitfenster; die abgewaehlten
 * Stunden fallen aus dem Plan und kommen auch beim naechsten Abruf nicht zurueck.
 */
export function LessonChoicesCard({ onSaved }: LessonChoicesCardProps) {
  const [gruppen, setGruppen] = useState<LessonChoiceGroup[]>([]);
  const [auswahl, setAuswahl] = useState<Record<string, number[]>>({});
  const [speichert, setSpeichert] = useState(false);
  const [fehler, setFehler] = useState<string | null>(null);
  const [hinweis, setHinweis] = useState<string | null>(null);

  useEffect(() => {
    let abgebrochen = false;
    void getLessonChoices()
      .then((geladen) => {
        if (abgebrochen) return;
        setGruppen(geladen);
        setAuswahl(auswahlAus(geladen));
      })
      // Ohne Parallelstunden ist die Karte nur nicht da; ein Ladefehler ist hier kein Grund, die Seite zu stoeren.
      .catch(() => undefined);
    return () => {
      abgebrochen = true;
    };
  }, []);

  if (gruppen.length === 0) return null;

  const offen = gruppen.filter((gruppe) => !gruppe.decided).length;
  const unvollstaendig = gruppen.some((gruppe) => (auswahl[gruppe.key] ?? []).length === 0);

  /**
   * Gruppen mit denselben Faechern (Doppelstunden, mehrere Tage) werden
   * gemeinsam umgeschaltet: wer Latein statt Franzoesisch besucht, besucht es
   * in jedem dieser Zeitfenster, und niemand will dieselbe Frage zehnmal beantworten.
   */
  function umschalten(gruppe: LessonChoiceGroup, fachId: number) {
    setHinweis(null);
    const faecher = gruppe.options.map((o) => o.subject_id).sort().join(",");
    setAuswahl((aktuell) => {
      const besucht = aktuell[gruppe.key] ?? [];
      const hinzu = !besucht.includes(fachId);
      const naechste = { ...aktuell };
      for (const andere of gruppen) {
        if (andere.options.map((o) => o.subject_id).sort().join(",") !== faecher) continue;
        const liste = (aktuell[andere.key] ?? []).filter((id) => id !== fachId);
        naechste[andere.key] = hinzu ? [...liste, fachId] : liste;
      }
      return naechste;
    });
  }

  async function speichern() {
    setSpeichert(true);
    setFehler(null);
    setHinweis(null);
    try {
      const ergebnis = await saveLessonChoices(auswahl);
      setGruppen(ergebnis.groups);
      setAuswahl(auswahlAus(ergebnis.groups));
      setHinweis(ergebnis.resynced ? "Gespeichert. Der Stundenplan wurde neu abgerufen." : "Gespeichert. Nicht besuchte Stunden sind aus dem Plan entfernt.");
      onSaved();
    } catch (error) {
      setFehler(fehlertext(error));
    } finally {
      setSpeichert(false);
    }
  }

  return (
    <Card className="border-border/80 bg-card/90">
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-3 space-y-0 pb-3">
        <div>
          <CardTitle>Parallele Stunden</CardTitle>
          <CardDescription className="mt-1">
            Diese Fächer liegen im Plan zur selben Zeit. Hake an, was du besuchst – nur das wird eingetragen.
          </CardDescription>
        </div>
        {offen > 0 ? <Badge variant="secondary">{offen} offen</Badge> : null}
      </CardHeader>
      <CardContent className="space-y-4">
        <ul className="space-y-3">
          {gruppen.map((gruppe) => (
            <li key={gruppe.key} className="border border-border p-3">
              <p className="text-xs font-semibold text-muted-foreground">
                {WOCHENTAGE[gruppe.weekday] ?? "Tag"}, {gruppe.start}–{gruppe.end}
                {gruppe.decided ? null : <span className="ml-2 font-medium">· noch nicht gewählt</span>}
              </p>
              <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1.5">
                {gruppe.options.map((option) => (
                  <label key={option.subject_id} className="flex items-center gap-2 text-[13px]">
                    <input
                      type="checkbox"
                      className="h-4 w-4 accent-foreground"
                      checked={(auswahl[gruppe.key] ?? []).includes(option.subject_id)}
                      onChange={() => umschalten(gruppe, option.subject_id)}
                      disabled={speichert}
                    />
                    <span className="font-medium">{option.name}</span>
                  </label>
                ))}
              </div>
            </li>
          ))}
        </ul>

        <FormError message={unvollstaendig ? "Wähle in jedem Zeitfenster mindestens ein Fach." : fehler} />
        {hinweis ? (
          <p className="text-[13px] text-muted-foreground" role="status">
            {hinweis}
          </p>
        ) : null}

        <Button type="button" onClick={() => void speichern()} disabled={speichert || unvollstaendig}>
          {speichert ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <CheckCircle2 aria-hidden="true" />}
          {speichert ? "Wird gespeichert …" : "Auswahl speichern"}
        </Button>
      </CardContent>
    </Card>
  );
}
