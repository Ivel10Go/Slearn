"use client";

import { useRef, useState } from "react";
import { CheckCircle2, FileUp, LoaderCircle, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { FormError } from "@/components/ui/field";
import {
  applyTimetable,
  extractTimetable,
  type TimetableApplied,
  type TimetableEntryDraft,
  type TimetableExtraction,
} from "@/lib/timetable-upload";
import { t } from "@/lib/i18n";

const WOCHENTAGE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"];
const ERLAUBT = ".pdf,.png,.jpg,.jpeg,.webp";

interface TimetableUploadCardProps {
  /** Nach dem Uebernehmen: Faecherliste neu laden. */
  onApplied: () => void;
  /** Ob die Karte offen steht; der Aufrufer entscheidet, weil er die Faecherzahl kennt. */
  open: boolean;
  onToggle: () => void;
}

function fehlertext(error: unknown): string {
  return error instanceof Error ? error.message : t("Das hat nicht geklappt.");
}

/**
 * Stundenplan als PDF oder Bild hochladen.
 *
 * Zwei Schritte, damit nichts ungeprueft landet: "Auslesen" zeigt nur einen
 * Vorschlag, erst "Uebernehmen" schreibt. Einzelne Stunden lassen sich vorher
 * entfernen, weil ein Modell aus einem Foto auch Unsinn lesen kann.
 */
export function TimetableUploadCard({ onApplied, open, onToggle }: TimetableUploadCardProps) {
  const [datei, setDatei] = useState<File | null>(null);
  const [vorschlag, setVorschlag] = useState<TimetableExtraction | null>(null);
  const [eintraege, setEintraege] = useState<TimetableEntryDraft[]>([]);
  const [arbeitet, setArbeitet] = useState<"lesen" | "speichern" | null>(null);
  const [fehler, setFehler] = useState<string | null>(null);
  const [ergebnis, setErgebnis] = useState<TimetableApplied | null>(null);
  const eingabe = useRef<HTMLInputElement>(null);

  function zuruecksetzen() {
    setDatei(null);
    setVorschlag(null);
    setEintraege([]);
    setFehler(null);
    if (eingabe.current) eingabe.current.value = "";
  }

  async function auslesen() {
    if (!datei) return;
    setArbeitet("lesen");
    setFehler(null);
    setErgebnis(null);
    try {
      const gelesen = await extractTimetable(datei);
      setVorschlag(gelesen);
      setEintraege(gelesen.entries);
    } catch (error) {
      setVorschlag(null);
      setFehler(fehlertext(error));
    } finally {
      setArbeitet(null);
    }
  }

  async function uebernehmen() {
    if (eintraege.length === 0) return;
    setArbeitet("speichern");
    setFehler(null);
    try {
      setErgebnis(await applyTimetable(eintraege));
      zuruecksetzen();
      onApplied();
    } catch (error) {
      setFehler(fehlertext(error));
    } finally {
      setArbeitet(null);
    }
  }

  const neueFaecher = vorschlag
    ? vorschlag.subjects.filter((fach) => !fach.exists && eintraege.some((e) => e.subject.toLowerCase() === fach.name.toLowerCase()))
    : [];
  const nachTag = WOCHENTAGE.map((name, tag) => ({
    name,
    stunden: eintraege.filter((e) => e.weekday === tag).sort((a, b) => a.start.localeCompare(b.start)),
  })).filter((tag) => tag.stunden.length > 0);

  return (
    <Card className="border-border/80 bg-card/90">
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-3 space-y-0 pb-3">
        <div>
          <CardTitle>{t("Stundenplan hochladen")}</CardTitle>
          <CardDescription className="mt-1">
            {t("Kein Schulportal? Lade deinen Plan als PDF oder Bild hoch. Fächer und Stunden werden daraus übernommen.")}
          </CardDescription>
        </div>
        <Button type="button" size="sm" variant="outline" onClick={onToggle} aria-expanded={open}>
          {open ? t("Schließen") : t("Öffnen")}
        </Button>
      </CardHeader>

      {open ? (
        <CardContent className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <input
              ref={eingabe}
              type="file"
              accept={ERLAUBT}
              aria-label={t("Stundenplan als PDF oder Bild wählen")}
              className="max-w-full text-[13px] file:mr-3 file:rounded-md file:border file:border-border file:bg-card file:px-3 file:py-1.5 file:text-[13px] file:font-medium"
              disabled={arbeitet !== null}
              onChange={(event) => {
                setDatei(event.target.files?.[0] ?? null);
                setVorschlag(null);
                setEintraege([]);
                setFehler(null);
              }}
            />
            <Button type="button" onClick={() => void auslesen()} disabled={!datei || arbeitet !== null}>
              {arbeitet === "lesen" ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <FileUp aria-hidden="true" />}
              {arbeitet === "lesen" ? t("Wird gelesen …") : t("Auslesen")}
            </Button>
          </div>
          <p className="text-xs leading-5 text-muted-foreground">
            {t("Das Auslesen läuft über deinen KI-Anbieter (unter „KI & Modelle“). Für Bilder braucht das Modell Bildeingabe.")}
          </p>

          <FormError message={fehler} />

          {ergebnis ? (
            <p className="text-[13px] text-muted-foreground" role="status">
              {t("{faecher} neue Fächer und {stunden} Stunden übernommen.", { faecher: ergebnis.created_subjects, stunden: ergebnis.imported_lessons })}
            </p>
          ) : null}

          {vorschlag ? (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center gap-2">
                <p className="text-sm font-semibold">{t("Gelesen: {n} Stunden", { n: eintraege.length })}</p>
                {neueFaecher.length > 0 ? <Badge variant="secondary">{t("{n} neue Fächer", { n: neueFaecher.length })}</Badge> : null}
              </div>
              {neueFaecher.length > 0 ? (
                <p className="text-xs leading-5 text-muted-foreground">
                  {t("Neu angelegt werden: {liste}.", { liste: neueFaecher.map((fach) => fach.name).join(", ") })}
                </p>
              ) : null}

              <div className="space-y-3">
                {nachTag.map((tag) => (
                  <div key={tag.name}>
                    <p className="text-xs font-semibold text-muted-foreground">{tag.name}</p>
                    <ul className="mt-1 divide-y divide-border border border-border">
                      {tag.stunden.map((stunde) => (
                        <li key={`${stunde.weekday}-${stunde.start}-${stunde.subject}`} className="flex items-center gap-3 px-3 py-1.5 text-[13px]">
                          <span className="font-mono text-xs text-muted-foreground">
                            {stunde.start}–{stunde.end}
                          </span>
                          <span className="min-w-0 flex-1 truncate font-medium">{stunde.subject}</span>
                          {stunde.room ? <span className="text-xs text-muted-foreground">{t("Raum {raum}", { raum: stunde.room })}</span> : null}
                          <Button
                            type="button"
                            size="icon"
                            variant="ghost"
                            aria-label={t("{fach} am {tag} um {zeit} entfernen", { fach: stunde.subject, tag: tag.name, zeit: stunde.start })}
                            disabled={arbeitet !== null}
                            onClick={() => setEintraege((liste) => liste.filter((e) => e !== stunde))}
                          >
                            <X aria-hidden="true" />
                          </Button>
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <Button type="button" onClick={() => void uebernehmen()} disabled={eintraege.length === 0 || arbeitet !== null}>
                  {arbeitet === "speichern" ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <CheckCircle2 aria-hidden="true" />}
                  {arbeitet === "speichern" ? t("Wird übernommen …") : t("Übernehmen")}
                </Button>
                <Button type="button" variant="ghost" onClick={zuruecksetzen} disabled={arbeitet !== null}>
                  {t("Verwerfen")}
                </Button>
              </div>
              <p className="text-xs leading-5 text-muted-foreground">
                {t("Der Plan gilt für die nächsten 8 Wochen. Stunden aus dem Schulportal bleiben unberührt.")}
              </p>
            </div>
          ) : null}
        </CardContent>
      ) : null}
    </Card>
  );
}
