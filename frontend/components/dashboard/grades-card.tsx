"use client";

import { useMemo, useState, type FormEvent } from "react";
import { FileText, GraduationCap, LoaderCircle, Plus, RefreshCw, ShieldAlert, Trash2, TrendingUp, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Field, FormError } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { getDocuments, previewGradeImport, importGrades, type GradeWrite } from "@/lib/api";
import { formatDate, toIsoDate } from "@/lib/format";
import {
  GRADE_ART,
  bestandenLabel,
  grenzLabel,
  gewichtText,
  hochrechnungText,
  kopfZahl,
  notenAnzahl,
  notenText,
  schnittText,
  verlaufAchse,
  verlaufAltText,
  verlaufBeschriftung,
  verlaufPunkte,
  yFuer,
} from "@/lib/grades";
import { useAction, useList } from "@/lib/use-collection";
import { useGrades, type ImportInput } from "@/lib/use-grades";
import { useSubjectOptions } from "@/lib/use-subject-options";
import { cn } from "@/lib/utils";
import type { DocumentRecord, GradeImportPreview, GradeKind, GradeSharing, GradeTrendPoint } from "@/lib/types";

/**
 * Die Notenkarte des Dashboards.
 *
 * Sie steht hier und nicht unter `/lernen`, weil sie eine Frage beantwortet, die
 * nur der Tagesueberblick stellen kann: *wie stehe ich?* Schnitt, Verlauf und
 * Hochrechnung sind eine einzige Aussage ueber den **Zustand**, und dieser
 * Zustand ist genau das, was ein Tagesbriefing liefert. Die Fortschrittskarte
 * macht es mit Konfidenz und Serie genauso, aus demselben Grund.
 *
 * Das Schreiben gehoert trotzdem hierher, und zwar aus zwei Gruenden: eine Karte,
 * die eine Zahl anzeigt, ohne sie aendern zu duerfen, braucht einen zweiten Weg
 * ("unter /lernen pflegen"), und es gaebe dann zwei Orte fuer dieselbe Sache.
 * Beide Formulare - von Hand und aus einem Dokument - sind **zugeklappt** und
 * werden erst gemountet, wenn sie offen sind. Erst dann holen sie ihre Faecher
 * und Dokumente; eine dauerhaft offene Dokumentliste waere ein Request, den
 * niemand braucht.
 *
 * Die Gestaltung folgt der Regel des Projekts: eckig, monochrom, Haarlinien.
 * **Eine "rote Note" gibt es hier nicht.** Notenstaerke steht ueber Gewicht,
 * Position im Verlauf und deutsche Woerter - eine Ampelfarbe waere fuer eine
 * Leistungsbeurteilung auch das falsche Mittel. Die Grenze bei 4,0 ist eine
 * zweite Haarlinie, "nicht bestanden" ist ein Wort, und ein nicht bestandenes
 * Fach traegt eine Unterstreichung.
 *
 * **Keine Diagrammbibliothek.** Die Linie ist ein `polyline` in einem
 * `viewBox` von Hand, und `vector-effect="non-scaling-stroke"` haelt die
 * Strichstaerke auch dann duenn, wenn die Grafik auf die Kartenbreite gestreckt
 * wird. Ein Chart-Paket waere fuer zwei Punkte ein Megabyte, und die CSP in
 * `next.config.ts` laesst ein fremdes Skript nicht ohne Ausnahmegenehmigung zu.
 */

/** Feste Zeichenflaeche der Linie; `viewBox` in Punkten, nicht in Pixeln. */
const BREITE = 400;
const HOEHE = 64;

/** Obergrenze des Offen-Feldes, dieselbe Grenze wie im Backend. */
const MAX_OFFEN = 100;

function Kennzahl({ titel, wert, zusatz }: { titel: string; wert: string; zusatz: string }) {
  return (
    <div className="min-w-0">
      <p className="text-[11px] font-medium text-muted-foreground">{titel}</p>
      <p className="mt-1 truncate text-base font-semibold tracking-tight">{wert}</p>
      <p className="mt-0.5 text-xs text-muted-foreground">{zusatz}</p>
    </div>
  );
}

/**
 * Die Feldeigenschaften, die `Field` einem Bedienelement uebergibt.
 *
 * `ArtWahl` nimmt sie als Parameter entgegen und **leitet sie weiter** - ein
 * Auswahlfeld, das `aria-describedby` verliert, verweist seinen Screenreader
 * nicht mehr auf seinen Hinweis.
 */
type FeldProps = {
  id: string;
  "aria-invalid": true | undefined;
  "aria-describedby": string | undefined;
  required: true | undefined;
};

/** Die Auswahl der Art, an zwei Stellen gebraucht und sonst zweimal getippt. */
function ArtWahl({ props, wert, setWert, disabled }: { props: FeldProps; wert: GradeKind; setWert: (art: GradeKind) => void; disabled?: boolean }) {
  return (
    <Select {...props} value={wert} onChange={(event) => setWert(event.target.value as GradeKind)} disabled={disabled}>
      {(Object.keys(GRADE_ART) as GradeKind[]).map((schluessel) => (
        <option key={schluessel} value={schluessel}>
          {GRADE_ART[schluessel]}
        </option>
      ))}
    </Select>
  );
}

/**
 * Die Verlaufslinie, von Hand gebaut.
 *
 * Die Y-Achse ist **fest** (1 bis mindestens zur Bestehensgrenze) und die
 * X-Achse verteilt die **Tage** - nicht die Noten. Beides steht in
 * `lib/grades.ts` und wird dort getestet; hier kommt nur die Zeichnung dazu.
 *
 * Der Haken am letzten Punkt ist ein senkrechter Strich statt eines Punktes:
 * `preserveAspectRatio="none"` streckt die Grafik nur in der Breite, und ein
 * Kreis wuerde dabei zu einer Ellipse. Ein senkrechter Strich ueberlebt das
 * unvertauscht. Bei genau einer Note gibt es keine Linie, sondern nur den Haken -
 * eine Linie zwischen einem Punkt und sich selbst waere eine Behauptung.
 */
function VerlaufLinie({ trend, passingGrade }: { trend: GradeTrendPoint[]; passingGrade: number }) {
  const { min, max } = verlaufAchse(trend, passingGrade);
  const punkte = verlaufPunkte(trend, BREITE, HOEHE, min, max);
  const grenzY = yFuer(min, max, HOEHE, passingGrade);
  const letzter = punkte[punkte.length - 1];
  return (
    <svg
      viewBox={`0 0 ${BREITE} ${HOEHE}`}
      preserveAspectRatio="none"
      className="h-16 w-full text-primary"
      role="img"
      aria-label={verlaufAltText(trend, passingGrade)}
    >
      {/* Die Bestehensgrenze als zweite Haarlinie. Ohne ihre Beschriftung waere
          sie eine Markierung ohne Aussage. */}
      <line x1={0} y1={grenzY} x2={BREITE} y2={grenzY} stroke="currentColor" strokeWidth={1} opacity={0.3} vectorEffect="non-scaling-stroke" />
      {punkte.length > 1 ? (
        <polyline
          points={punkte.map((punkt) => `${punkt.x},${punkt.y}`).join(" ")}
          fill="none"
          stroke="currentColor"
          strokeWidth={1.5}
          strokeLinejoin="miter"
          strokeLinecap="butt"
          vectorEffect="non-scaling-stroke"
        />
      ) : null}
      {letzter ? (
        <line
          x1={letzter.x}
          y1={letzter.y - 4}
          x2={letzter.x}
          y2={letzter.y + 4}
          stroke="currentColor"
          strokeWidth={1.5}
          vectorEffect="non-scaling-stroke"
        />
      ) : null}
    </svg>
  );
}

/** Eine Zeile in der Fachliste. */
function FachZeile({ name, average, count, passed, gewicht }: { name: string; average: number; count: number; passed: boolean; gewicht: number }) {
  return (
    <li className="flex items-baseline justify-between gap-3">
      <div className="min-w-0">
        {/* Die Unterstreichung ist das einzige Warnzeichen der Karte - kein
            Rot, kein Symbol. Genau so unterscheidet das Projekt Zustände. */}
        <p className={cn("truncate text-sm font-semibold", !passed && "underline underline-offset-4")}>{name}</p>
        <p className="text-[11px] text-muted-foreground">
          {notenAnzahl(count)}
          {gewicht === count ? "" : ` · Gewicht ${gewicht}`}
        </p>
      </div>
      <p className="flex shrink-0 items-baseline gap-2">
        <span className="text-sm font-semibold tabular-nums">{notenText(average)}</span>
        <Badge variant={passed ? "outline" : "default"}>{bestandenLabel(passed)}</Badge>
      </p>
    </li>
  );
}

/**
 * Die manuelle Erfassung.
 *
 * Eigene Komponente und deshalb **erst gemountet, wenn sie offen ist**: sie
 * braucht eine Fachliste, und die soll nicht bei jedem Dashboard-Aufruf geladen
 * werden, nur weil jemand vielleicht eine Note eintragen will.
 */
function NotenFormular({ laeuft, onSchliessen, onSpeichern }: { laeuft: boolean; onSchliessen: () => void; onSpeichern: (eingabe: GradeWrite) => Promise<unknown> }) {
  const fach = useSubjectOptions();
  const [wert, setWert] = useState("2,0");
  const [art, setArt] = useState<GradeKind>("written_exam");
  const [gewicht, setGewicht] = useState("1");
  const [datum, setDatum] = useState(() => toIsoDate(new Date()));
  const [fachId, setFachId] = useState("");
  const [kommentar, setKommentar] = useState("");
  const [fehler, setFehler] = useState<string | null>(null);

  async function absenden(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    // "2,0" und "2.0" sind beide eine Note, alles andere nicht.
    const zahl = Number(wert.trim().replace(",", "."));
    if (!Number.isFinite(zahl) || zahl <= 0 || zahl > 6) {
      setFehler("Die Note muss zwischen 1,0 und 6,0 liegen.");
      return;
    }
    const faktor = Number(gewicht);
    if (!Number.isInteger(faktor) || faktor < 1 || faktor > 20) {
      setFehler("Die Gewichtung muss zwischen 1 und 20 liegen.");
      return;
    }
    if (!fachId) {
      setFehler("Wähle das Fach, um das es geht.");
      return;
    }
    if (!datum) {
      setFehler("Wann wurde die Leistung erbracht? Ein Datum wird benötigt.");
      return;
    }
    setFehler(null);
    await onSpeichern({
      subject_id: Number(fachId),
      grade_value: Math.round(zahl * 10) / 10,
      kind: art,
      weight: faktor,
      graded_on: datum,
      comment: kommentar.trim() || null,
    });
    setKommentar("");
  }

  return (
    <form onSubmit={absenden} noValidate className="space-y-4 border border-border p-4">
      <div className="flex items-start justify-between gap-3">
        <p className="text-sm font-semibold">Note eintragen</p>
        <Button type="button" size="icon" variant="ghost" onClick={onSchliessen} disabled={laeuft} aria-label="Formular schließen">
          <X aria-hidden="true" />
        </Button>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <Field id="note-wert" label="Note" required hint="Von 1,0 bis 6,0.">
          {(props) => (
            <Input {...props} value={wert} onChange={(event) => setWert(event.target.value)} disabled={laeuft} inputMode="decimal" placeholder="2,0" />
          )}
        </Field>
        <Field id="note-art" label="Art" required>
          {(props) => <ArtWahl props={props} wert={art} setWert={setArt} disabled={laeuft} />}
        </Field>
        <Field id="note-fach" label="Fach" required hint={fach.status === "loading" ? "Fächer werden geladen …" : undefined}>
          {(props) => (
            <Select {...props} value={fachId} onChange={(event) => setFachId(event.target.value)} disabled={laeuft || fach.status === "loading"}>
              <option value="">Fach wählen …</option>
              {fach.options.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.name}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <div className="grid grid-cols-2 gap-4">
          <Field id="note-datum" label="Datum" required>
            {(props) => <Input {...props} type="date" value={datum} onChange={(event) => setDatum(event.target.value)} disabled={laeuft} />}
          </Field>
          <Field id="note-gewicht" label="Gewicht" hint="Zählt öfter.">
            {(props) => <Input {...props} type="number" min={1} max={20} value={gewicht} onChange={(event) => setGewicht(event.target.value)} disabled={laeuft} />}
          </Field>
        </div>
      </div>

      <Field id="note-kommentar" label="Kommentar">
        {(props) => <Input {...props} value={kommentar} onChange={(event) => setKommentar(event.target.value)} disabled={laeuft} placeholder="Nur mit viel Übung" />}
      </Field>

      <FormError message={fehler} />

      <div className="flex flex-wrap items-center gap-2">
        <Button type="submit" disabled={laeuft}>
          {laeuft ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <Plus aria-hidden="true" />}
          {laeuft ? "Wird gespeichert …" : "Note speichern"}
        </Button>
        <Button type="button" variant="ghost" onClick={onSchliessen} disabled={laeuft}>
          Abbrechen
        </Button>
      </div>
      <p className="text-xs leading-5 text-muted-foreground">
        Die Note geht in den Schnitt ihres Faches ein. Steht das Fach über der Bestehensgrenze, hebt sie dort die Priorität der
        Lernaufgaben an.
      </p>
    </form>
  );
}

/**
 * Die Uebernahme aus einem abgegebenen Dokument - bewusst in **zwei Schritten**.
 *
 * Zuerst wird gezeigt, was der Server in der Datei liest, dann legt er nur die
 * angekreuzten Zeilen an. Ein Notenspiegel wird falsch gelesen, und eine
 * Notenliste, die sich nicht zuruecknehmen laesst, waere ein Grund, die
 * Funktion gar nicht erst anzubieten. Angekreuzt sind vorab alle Zeilen, die
 * noch nicht importiert sind.
 */
function NotenImport({ laeuft, onGeaendert }: { laeuft: boolean; onGeaendert: () => void }) {
  const dokumente = useList<DocumentRecord>(() => getDocuments());
  const fach = useSubjectOptions();
  const vorschau = useAction<GradeImportPreview>();
  const ergebnis = useAction<{ created: number; skipped: number }>();
  const [dokumentId, setDokumentId] = useState("");
  const [fachId, setFachId] = useState("");
  const [art, setArt] = useState<GradeKind>("written_exam");
  const [gewicht, setGewicht] = useState("1");
  const [datum, setDatum] = useState(() => toIsoDate(new Date()));
  const [bestaetigt, setBestaetigt] = useState<string[] | null>(null);

  /** Die Vorgaben, die Vorschau und Uebernahme **gemeinsam** benutzen muessen. */
  function vorgabe(): ImportInput {
    return {
      documentId: Number(dokumentId),
      subjectId: fachId ? Number(fachId) : undefined,
      defaultKind: art,
      defaultWeight: Number(gewicht) || 1,
      defaultDate: datum,
    };
  }

  async function zeilenLaden() {
    const gefunden = await vorschau.run(() => previewGradeImport(vorgabe()));
    if (gefunden) {
      setBestaetigt(gefunden.entries.filter((zeile) => !zeile.already_imported).map((zeile) => zeile.key));
    }
  }

  async function uebernehmen() {
    if (bestaetigt === null || bestaetigt.length === 0) {
      return;
    }
    const result = await ergebnis.run(() => importGrades({ ...vorgabe(), keys: bestaetigt }));
    if (result) {
      onGeaendert();
      setBestaetigt(null);
      vorschau.reset();
    }
  }

  return (
    <div className="space-y-4 border border-border p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm font-semibold">Aus einem Dokument übernehmen</p>
        <Badge variant="secondary">erst ansehen, dann anlegen</Badge>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <Field
          id="import-dokument"
          label="Dokument"
          required
          hint={dokumente.status === "loading" ? "Dokumente werden geladen …" : "Eine hochgeladene Textdatei mit deinem Notenspiegel."}
        >
          {(props) => (
            <Select
              {...props}
              value={dokumentId}
              onChange={(event) => {
                setDokumentId(event.target.value);
                setBestaetigt(null);
                vorschau.reset();
                ergebnis.reset();
              }}
              disabled={laeuft || dokumente.status === "loading"}
            >
              <option value="">Dokument wählen …</option>
              {dokumente.items.map((dokument) => (
                <option key={dokument.id} value={dokument.id}>
                  {dokument.filename}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field id="import-fach" label="Fach für Zeilen ohne Namen" hint="Greift nur, wenn die Zeile kein Fach nennt.">
          {(props) => (
            <Select {...props} value={fachId} onChange={(event) => setFachId(event.target.value)} disabled={laeuft || fach.status === "loading"}>
              <option value="">Kein Vorgabefach</option>
              {fach.options.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.name}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field id="import-art" label="Art ohne Angabe">
          {(props) => <ArtWahl props={props} wert={art} setWert={setArt} disabled={laeuft} />}
        </Field>
        <div className="grid grid-cols-2 gap-4">
          <Field id="import-gewicht" label="Gewicht">
            {(props) => <Input {...props} type="number" min={1} max={20} value={gewicht} onChange={(event) => setGewicht(event.target.value)} disabled={laeuft} />}
          </Field>
          <Field id="import-datum" label="Datum ohne Angabe">
            {(props) => <Input {...props} type="date" value={datum} onChange={(event) => setDatum(event.target.value)} disabled={laeuft} />}
          </Field>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" size="sm" variant="outline" onClick={() => void zeilenLaden()} disabled={laeuft || !dokumentId || vorschau.pending}>
          {vorschau.pending ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <FileText aria-hidden="true" />}
          {vorschau.pending ? "Wird gelesen …" : "Zeilen anzeigen"}
        </Button>
        {bestaetigt ? (
          <Button type="button" size="sm" onClick={() => void uebernehmen()} disabled={laeuft || ergebnis.pending || bestaetigt.length === 0}>
            {ergebnis.pending ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : null}
            {bestaetigt.length} {bestaetigt.length === 1 ? "Note" : "Noten"} übernehmen
          </Button>
        ) : null}
      </div>

      <FormError message={vorschau.error ?? ergebnis.error} />

      {ergebnis.result ? (
        <p className="border border-foreground/20 bg-foreground/5 px-3 py-2 text-sm font-semibold" role="status">
          {ergebnis.result.created} {ergebnis.result.created === 1 ? "Note" : "Noten"} angelegt
          {ergebnis.result.skipped > 0 ? `, ${ergebnis.result.skipped} waren schon da` : ""}.
        </p>
      ) : null}

      {dokumente.status === "ready" && dokumente.items.length === 0 ? (
        <p className="border border-dashed border-border px-3 py-2 text-xs leading-5 text-muted-foreground">
          Es ist noch kein Dokument vorhanden. Lade unter <span className="font-semibold">Dokumente</span> eine Textdatei mit deinem
          Notenspiegel hoch.
        </p>
      ) : null}

      {vorschau.result ? (
        vorschau.result.entries.length === 0 ? (
          <EmptyState
            icon={FileText}
            title="Keine Noten gefunden"
            description="In diesem Dokument steht keine Zeile mit einer Zahl zwischen 1,0 und 6,0. Was nicht erkannt wird, wird nicht geraten."
          />
        ) : (
          <ul className="space-y-2">
            {vorschau.result.entries.map((zeile) => {
              const gewaehlt = bestaetigt?.includes(zeile.key) ?? false;
              return (
                <li key={zeile.key} className="flex items-start gap-3 border border-border bg-card/60 px-3 py-2.5">
                  <input
                    type="checkbox"
                    id={`import-zeile-${zeile.key}`}
                    className="mt-1 h-4 w-4 shrink-0 accent-foreground"
                    checked={gewaehlt}
                    disabled={zeile.already_imported || laeuft}
                    onChange={(event) =>
                      setBestaetigt((aktuell) => {
                        const menge = new Set(aktuell ?? []);
                        if (event.target.checked) {
                          menge.add(zeile.key);
                        } else {
                          menge.delete(zeile.key);
                        }
                        return [...menge];
                      })
                    }
                  />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-semibold">
                      {notenText(zeile.value)} · {zeile.subject_name ?? "Fach nicht erkannt"}
                    </p>
                    <p className="text-[11px] text-muted-foreground">
                      {GRADE_ART[zeile.kind]} · {formatDate(zeile.graded_on)}
                      {zeile.subject_recognized ? "" : " · Vorgabefach"}
                      {zeile.already_imported ? " · schon importiert" : ""}
                    </p>
                    {zeile.comment ? <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{zeile.comment}</p> : null}
                  </div>
                  <label htmlFor={`import-zeile-${zeile.key}`} className="sr-only">
                    Zeile {zeile.line_number} übernehmen: {zeile.line}
                  </label>
                </li>
              );
            })}
          </ul>
        )
      ) : null}
    </div>
  );
}

/**
 * Die Zustimmung fuer den KI-Dienst.
 *
 * Sie steht in der Notenkarte und nicht in den Einstellungen, weil sie eine
 * Frage der Noten beantwortet: wohin diese Zahlen gehen duerfen. Wer sie nur
 * unter "Einstellungen" findet, hat sie nicht gefunden.
 *
 * Der Schalter ist ein Klick, kein Formular: es gibt genau zwei Zustaende, und
 * "nein" ist der Normalfall.
 */
function FreigabeSchalter({ sharing, laeuft, onAendern }: { sharing: GradeSharing | null; laeuft: boolean; onAendern: (enabled: boolean) => void }) {
  const aktiv = sharing?.share_grades_with_ai ?? false;
  const orte = sharing?.uses.join(", ");
  return (
    <div className="border border-border bg-muted/30 p-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="flex items-center gap-2 text-xs font-semibold">
          <ShieldAlert className="h-4 w-4 shrink-0" aria-hidden="true" />
          Noten an den KI-Dienst senden
        </p>
        <Badge variant={aktiv ? "default" : "outline"}>{aktiv ? "Ja, ausdrücklich" : "Nein"}</Badge>
      </div>
      <p className="mt-2 text-xs leading-5 text-muted-foreground">
        {aktiv
          ? `Deine Noten gehen an den Assistenten und stehen im Willkommenssatz${orte ? `: ${orte}` : ""}. Ohne verbundenen Provider wird nichts verschickt.`
          : "Ohne diese Zustimmung verlassen deine Noten den Rechner nicht — weder der Assistent noch der Willkommenssatz sehen sie. Schnitt und Hochrechnung oben rechnen trotzdem, sie stehen nur nicht im Prompt."}
      </p>
      <Button type="button" size="sm" variant="outline" className="mt-3" onClick={() => onAendern(!aktiv)} disabled={laeuft} aria-pressed={aktiv}>
        {laeuft ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : null}
        {aktiv ? "Freigabe zurücknehmen" : "Freigabe erteilen"}
      </Button>
    </div>
  );
}

export function GradesCard() {
  const noten = useGrades();
  const [formularOffen, setFormularOffen] = useState(false);
  const [importOffen, setImportOffen] = useState(false);
  const [offen, setOffen] = useState("0");
  const zusammenfassung = noten.summary;

  const gefaecht = useMemo(() => zusammenfassung?.by_subject.filter((fach) => fach.subject_id !== null).length ?? 0, [zusammenfassung]);
  const durchgefallen = useMemo(() => zusammenfassung?.by_subject.filter((fach) => !fach.passed).length ?? 0, [zusammenfassung]);

  function hochrechnungBerechnen(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const zahl = Number(offen);
    noten.setProjectedWeight(Number.isInteger(zahl) && zahl >= 0 && zahl <= MAX_OFFEN ? zahl : 0);
  }

  if (noten.status === "loading" && !zusammenfassung) {
    return (
      <Card>
        <CardHeader className="pb-4">
          <CardTitle>Noten</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3" aria-busy="true" aria-label="Noten werden geladen">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-24 w-full" />
        </CardContent>
      </Card>
    );
  }

  if (!zusammenfassung) {
    return (
      <Card role="alert">
        <CardHeader className="pb-4">
          <CardTitle>Noten</CardTitle>
        </CardHeader>
        <CardContent>
          <Button type="button" size="sm" variant="outline" onClick={() => void noten.reload()}>
            <RefreshCw aria-hidden="true" />
            Erneut versuchen
          </Button>
        </CardContent>
      </Card>
    );
  }

  const ohneNoten = zusammenfassung.total_count === 0;
  const neueste = noten.grades[0];
  const hochrechnung = hochrechnungText(zusammenfassung.projection);

  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-3 space-y-0 pb-3">
        <div>
          <CardTitle>Noten</CardTitle>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant={ohneNoten ? "secondary" : zusammenfassung.overall.passed ? "outline" : "default"}>{kopfZahl(zusammenfassung)}</Badge>
          <Button type="button" size="icon" variant="ghost" onClick={() => void noten.reload()} aria-label="Noten neu laden" disabled={noten.status === "loading"}>
            <RefreshCw className={cn(noten.status === "loading" && "animate-spin")} aria-hidden="true" />
          </Button>
        </div>
      </CardHeader>

      <CardContent className="space-y-6">
        {noten.error ? (
          <p className="border border-destructive/20 bg-destructive/10 px-3 py-2 text-sm text-destructive" role="alert">
            {noten.error}
          </p>
        ) : null}
        <FormError message={noten.actionError} />

        {ohneNoten ? (
          <EmptyState
            icon={GraduationCap}
            title="Noch keine Noten"
            description="Trag eine Note ein oder übernimm sie aus einem hochgeladenen Notenspiegel. Erst mit Noten kann der Lernplan seine Prioritäten daran ausrichten."
          />
        ) : (
          <>
            <div className="grid gap-4 sm:grid-cols-3">
              <Kennzahl titel="Schnitt gesamt" wert={schnittText(zusammenfassung.overall.average)} zusatz={bestandenLabel(zusammenfassung.overall.passed)} />
              <Kennzahl titel="Fächer" wert={`${gefaecht}`} zusatz={durchgefallen > 0 ? `${durchgefallen} nicht bestanden` : "alle bestanden"} />
              <Kennzahl
                titel="Zuletzt"
                wert={neueste ? notenText(neueste.grade_value) : "—"}
                zusatz={neueste ? `${GRADE_ART[neueste.kind]} · ${formatDate(neueste.graded_on, { day: "2-digit", month: "short" })}` : "—"}
              />
            </div>

            <div>
              <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
                <h3 className="text-xs font-medium text-muted-foreground">Verlauf des Schnitts</h3>
                <p className="text-xs text-muted-foreground">{verlaufBeschriftung(zusammenfassung.trend) ?? "noch kein Verlauf"}</p>
              </div>
              <VerlaufLinie trend={zusammenfassung.trend} passingGrade={zusammenfassung.passing_grade} />
              <p className="mt-1.5 text-[11px] text-muted-foreground">{grenzLabel(zusammenfassung.passing_grade)}</p>
            </div>

            {zusammenfassung.by_subject.length > 0 ? (
              <div>
                <h3 className="mb-3 text-xs font-medium text-muted-foreground">Schnitt je Fach</h3>
                <ul className="space-y-2.5">
                  {zusammenfassung.by_subject.map((fach) => (
                    <FachZeile
                      key={fach.subject_id ?? "ohne-fach"}
                      name={fach.subject_name}
                      average={fach.average}
                      count={fach.count}
                      passed={fach.passed}
                      gewicht={fach.weight}
                    />
                  ))}
                </ul>
              </div>
            ) : null}

            {/*
              Die Hochrechnung. Der Schnitt am Schuljahresende ist keine Note, die
              man kennt, sondern einer, den man sich erst noch verdienen muss -
              deshalb steht hier eine Rechnung und keine Ermahnung. Die Eingabe
              ist das **offene Gewicht** ("noch zwei Tests"), und ein Klick
              genuegt: bei jedem Tastendruck eine Anfrage zu schicken waere
              Raten, nicht Rechnen.
            */}
            <div>
              <h3 className="mb-3 text-xs font-medium text-muted-foreground">Hochrechnung</h3>
              <form onSubmit={hochrechnungBerechnen} noValidate className="flex flex-wrap items-end gap-3">
                <Field id="noten-offen" label="Noch offen" hint="Gewichtung der Leistungen, die noch kommen." className="w-44">
                  {(props) => <Input {...props} type="number" min={0} max={MAX_OFFEN} value={offen} onChange={(event) => setOffen(event.target.value)} disabled={noten.status === "loading"} />}
                </Field>
                <Button type="submit" size="sm" variant="outline" disabled={noten.status === "loading"}>
                  <TrendingUp aria-hidden="true" />
                  Hochrechnen
                </Button>
              </form>
              {hochrechnung ? (
                <p className="mt-3 border border-border bg-muted/30 px-3 py-2 text-sm">{hochrechnung}</p>
              ) : (
                <p className="mt-3 text-xs leading-5 text-muted-foreground">
                  Trage oben ein, was noch aussteht — die Karte nennt dann den Schnitt, der ab jetzt nötig wäre.
                </p>
              )}
            </div>

            <div>
              <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
                <h3 className="text-xs font-medium text-muted-foreground">Zuletzt notiert</h3>
                <p className="text-xs text-muted-foreground">{notenAnzahl(zusammenfassung.total_count)}</p>
              </div>
              {noten.grades.length === 0 ? (
                <p className="text-xs text-muted-foreground">Die Liste konnte nicht geladen werden.</p>
              ) : (
                <ul className="max-h-72 space-y-2 overflow-y-auto pr-1">
                  {noten.grades.map((note) => (
                    <li key={note.id} className="flex items-start gap-3 border border-border bg-card/60 px-3 py-2.5">
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-semibold">
                          {notenText(note.grade_value)} · {note.subject_name ?? "Ohne Fach"}
                        </p>
                        <p className="text-[11px] text-muted-foreground">
                          {GRADE_ART[note.kind]} · {formatDate(note.graded_on)}
                          {gewichtText(note.weight) ? ` · ${gewichtText(note.weight)}` : ""}
                          {note.source !== "manual" ? " · aus dem Dokument" : ""}
                        </p>
                        {note.comment ? <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{note.comment}</p> : null}
                      </div>
                      <Button
                        type="button"
                        size="icon"
                        variant="ghost"
                        onClick={() => void noten.loeschen(note.id)}
                        aria-label={`Note ${notenText(note.grade_value)} in ${note.subject_name ?? "ohne Fach"} löschen`}
                        disabled={noten.pending !== null}
                      >
                        {noten.pending === `delete:${note.id}` ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <Trash2 aria-hidden="true" />}
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </>
        )}

        <FreigabeSchalter sharing={noten.sharing} laeuft={noten.pending === "sharing"} onAendern={(enabled) => void noten.freigabeSetzen(enabled)} />

        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" size="sm" onClick={() => setFormularOffen((war) => !war)} aria-expanded={formularOffen}>
            <Plus aria-hidden="true" />
            Note erfassen
          </Button>
          <Button type="button" size="sm" variant="outline" onClick={() => setImportOffen((war) => !war)} aria-expanded={importOffen}>
            <FileText aria-hidden="true" />
            Aus Dokument übernehmen
          </Button>
        </div>

        {formularOffen ? (
          <NotenFormular
            laeuft={noten.pending === "create"}
            onSchliessen={() => setFormularOffen(false)}
            onSpeichern={async (eingabe) => {
              const gespeichert = await noten.speichern(eingabe);
              if (gespeichert) {
                setFormularOffen(false);
              }
            }}
          />
        ) : null}

        {importOffen ? <NotenImport laeuft={noten.pending === "import"} onGeaendert={() => void noten.reload()} /> : null}
      </CardContent>
    </Card>
  );
}
