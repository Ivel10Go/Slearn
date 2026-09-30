/**
 * Die deutschen Texte, die Zahlen und die Linie der Notenkarte.
 *
 * Alles hier ist rein: kein `fetch`, kein React, kein Diagramm. Deshalb steht
 * die Linie in dieser Datei und nicht in der Karte - sie ist eine Rechnung aus
 * Koordinaten, und eine Rechnung gehoert in eine Datei, die ein Test lesen kann.
 * `lib/progress.ts` macht es mit den Texten der Fortschrittskarte genauso, und
 * aus demselben Grund.
 *
 * **Keine Diagrammbibliothek.** Eine Linie aus zwei Punkten braucht kein
 * Bundle: ein `points`-Attribut auf einem `polyline` und ein `vector-effect`,
 * damit die Linie auch dann duenn bleibt, wenn die Grafik skaliert wird. Ein
 * Chart-Paket waere hier ein Megabyte Abhaengigkeit fuer eine Liste aus zwei
 * Punkten, und die strikte CSP in `next.config.ts` laesst ein fremdes Skript
 * ohnehin nur mit Ausnahmegenehmigung zu.
 *
 * Die Farbe ist nicht das Argument
 * ---------------------------------
 *
 * Die Karte ist monochrome und eckig, und Notenstaerke wird ueber **Gewicht,
 * Position und deutsche Woerter** unterschieden, nicht ueber eine Farbe. Eine
 * "rote Note" gibt es hier nicht, und zwar nicht aus Versehen: der Notenspiegel
 * eines Kindes ist etwas, das man nicht wie eine Ampel malt. Die Linie traegt
 * deshalb eine Haarlinie, die Grenze bei 4,0 ist eine zweite Haarlinie, und
 * "nicht bestanden" ist ein Wort.
 */

import type { GradeKind, GradeProjection, GradeSummary, GradeTrendPoint } from "./types";

/**
 * Die Art der Leistung in Worten.
 *
 * Bewusst hier und nicht im Backend: `GradeKind` ist ein Vertrag zwischen den
 * Enden, sein Anzeigename ist Darstellung. Dieselbe Regel wie bei
 * `STATUS_LABEL` in der Dokumentenliste.
 */
export const GRADE_ART: Record<GradeKind, string> = {
  written_exam: "Klassenarbeit",
  test: "Test",
  oral: "Mündlich",
};

/** Die beste Note der Skala. Die schlechteste ist 6,0 - die Datenbank laesst nichts darueber zu. */
export const BESTE_NOTE = 1;

/** "1,7" - mit Komma, wie man es in Deutschland schreibt. */
export function notenText(wert: number): string {
  return wert.toFixed(1).replace(".", ",");
}

/** Ein Schnitt, oder ein Gedankenstrich, wenn es ihn nicht gibt. */
export function schnittText(average: number | null | undefined): string {
  return average === null || average === undefined ? "—" : notenText(average);
}

/**
 * Bestanden oder nicht - zwei Woerter, keine dritte Stufe.
 *
 * "Knapp bestanden" koennte man sagen, aber jede Grenze dafuer waere
 * frei erfunden, und eine erfundene Schwelle in einer Karte ist eine
 * Behauptung. Die Nuance steht stattdessen in der Hochrechnung, wo sie eine
 * Zahl hat.
 */
export function bestandenLabel(passed: boolean): string {
  return passed ? "bestanden" : "nicht bestanden";
}

/** "2 Noten" oder "1 Note" - ohne Singular-Ausnahme im Aufrufer. */
export function notenAnzahl(count: number): string {
  return `${count} ${count === 1 ? "Note" : "Noten"}`;
}

/** Das Gewicht hinter einer Note, wenn es vom Default abweicht. */
export function gewichtText(weight: number): string | null {
  return weight === 1 ? null : `zählt ${weight}×`;
}

/**
 * Die Achse der Verlaufslinie.
 *
 * **Fest, nicht mitlaufend.** Sie deckt immer mindestens 1 bis zur
 * Bestehensgrenze ab - sonst wuerde die 4,0-Linie irgendwann aus dem Bild
 * rutschen und der Verlauf waere nur noch eine Form. Nach oben waechst sie mit,
 * wenn eine Note schlechter ist als der Schnitt; das geschieht selten genug,
 * dass die Linie nicht springt.
 *
 * Nach unten wird sie nie kleiner als 1: eine Achse, die mit einem guten
 * Schnitt auf 0,8 beginnt, wuerde jeden Unterschied plattziehen.
 */
export function verlaufAchse(
  trend: GradeTrendPoint[],
  passingGrade: number,
): { min: number; max: number } {
  const werte = trend.map((punkt) => punkt.average);
  const kleinster = werte.length > 0 ? Math.min(...werte) : BESTE_NOTE;
  const groesster = werte.length > 0 ? Math.max(...werte) : passingGrade;
  return {
    min: Math.min(BESTE_NOTE, Math.floor(kleinster)),
    max: Math.ceil(Math.max(passingGrade, groesster)),
  };
}

/** Die Y-Position eines Wertes auf der Achse. Klein ist gut, also oben. */
export function yFuer(min: number, max: number, hoehe: number, wert: number): number {
  const spanne = Math.max(0.1, max - min);
  const anteil = (wert - min) / spanne;
  return anteil * hoehe;
}

export interface VerlaufPunkt {
  x: number;
  y: number;
  average: number;
  graded_on: string;
}

/**
 * Die Punkte der Linie, in einem Koordinatenraum fester Groesse.
 *
 * Die X-Achse verteilt die **Tage**, nicht die Noten: zwischen zwei Einträgen
 * koennen Wochen liegen, und gleich breite Abstaende wuerden aus einem Monat
 * dieselbe Strecke machen wie aus einer Woche.
 *
 * Ein einzelner Punkt wird nicht zu einem Nullstrich: er kommt als Punkt
 * zurueck, damit die Karte etwas zeichnen kann, wo es nichts zu zeichnen gibt.
 */
export function verlaufPunkte(
  trend: GradeTrendPoint[],
  breite: number,
  hoehe: number,
  min: number,
  max: number,
): VerlaufPunkt[] {
  if (trend.length === 0) {
    return [];
  }
  const erster = Date.parse(`${trend[0].graded_on}T00:00:00Z`);
  const letzter = Date.parse(`${trend[trend.length - 1].graded_on}T00:00:00Z`);
  const spanne = Math.max(1, letzter - erster);
  return trend.map((punkt) => ({
    x: Math.round(((Date.parse(`${punkt.graded_on}T00:00:00Z`) - erster) / spanne) * (breite - 4) + 2),
    y: Math.round(yFuer(min, max, hoehe, punkt.average) * 100) / 100,
    average: punkt.average,
    graded_on: punkt.graded_on,
  }));
}

/**
 * Die Ueberschrift ueber der Linie: wie viele Noten, seit wann.
 *
 * Ohne Zeitraum waere ein Verlauf eine Behauptung. Deshalb steht er da, und
 * deshalb kommt er aus der Antwort und nicht aus dem Browser.
 */
export function verlaufBeschriftung(trend: GradeTrendPoint[]): string | null {
  if (trend.length === 0) {
    return null;
  }
  const erstes = trend[0].graded_on;
  const tage = Math.max(1, Math.round((Date.parse(`${trend[trend.length - 1].graded_on}T00:00:00Z`) - Date.parse(`${erstes}T00:00:00Z`)) / 86_400_000));
  const dauer = tage < 14 ? `${tage} Tage` : `${Math.round(tage / 7)} Wochen`;
  return `${notenAnzahl(trend[trend.length - 1].count)} über ${dauer}`;
}

/**
 * Die Hochrechnung in einem Satz - oder `null`, wenn es nichts zu rechnen gibt.
 *
 * Drei Faelle, und der dritte ist der wichtige: die noetige Note liegt
 * ausserhalb der Skala, also gibt es keine Zahl, die man nennen koennte. Dann
 * steht der Satz da, den man sagen muss, statt einer Zahl, die es nicht gibt.
 */
export function hochrechnungText(projection: GradeProjection): string | null {
  if (projection.projected_weight <= 0) {
    return null;
  }
  const grenze = notenText(projection.passing_grade);
  if (projection.needed_average === null) {
    return `Auch mit lauter 1,0 im Rest ist die ${grenze} nicht mehr drin.`;
  }
  const noetig = notenText(projection.needed_average);
  const bester = projection.best_possible_average === null ? null : notenText(projection.best_possible_average);
  const kern = `Ab ${noetig} im Rest klappt die ${grenze}.`;
  return bester === null ? kern : `${kern} Mit lauter 1,0 kämst du auf ${bester}.`;
}

/** Die Beschriftung der Schwelle auf der Linie. */
export function grenzLabel(passingGrade: number): string {
  return `${notenText(passingGrade)} · bestanden ab hier`;
}

/**
 * Was die Linie einem Screenreader oder einem Bildschirmfotos erzaehlt.
 *
 * Die Grafik selbst ist `role="img"` und traegt nur diesen einen Satz: eine
 * Punktefolge vorzulesen, ohne die Zahlen zu nennen, waere eine Bildbeschreibung
 * ohne Inhalt. Deshalb stehen hier Anfang, Ende und Zeitraum - alles, was man
 * zum Nachfragen braucht.
 */
export function verlaufAltText(trend: GradeTrendPoint[], passingGrade: number): string {
  if (trend.length === 0) {
    return "Noch kein Notenverlauf.";
  }
  const grenze = notenText(passingGrade);
  if (trend.length === 1) {
    return `Eine Note: ${notenText(trend[0].average)} am ${trend[0].graded_on}. Bestehensgrenze ${grenze}.`;
  }
  const erstes = trend[0];
  const letztes = trend[trend.length - 1];
  return `Schnittverlauf: ${notenText(erstes.average)} am ${erstes.graded_on} bis ${notenText(letztes.average)} am ${letztes.graded_on}, aus ${notenAnzahl(letztes.count)}. Bestehensgrenze ${grenze}.`;
}

/**
 * Wie viele der gelisteten Noten liegen im aktuellen Halbjahr.
 *
 * Bewusst nicht im Backend: der Schnitt ist eine Facette, und der Filter ist
 * eine Ansichtssache. Ein Halbjahr beginnt im Februar und August - an dieser
 * Stelle, nicht an zwei Orten im Code.
 */
export function aktuellesHalbjahr(jetzt: Date): { start: string; label: string } {
  const jahr = jetzt.getFullYear();
  const start = jetzt.getMonth() >= 7 ? new Date(jahr, 7, 1) : new Date(jahr, 1, 1);
  const ende = jetzt.getMonth() >= 7 ? "Januar" : "August";
  return {
    start: `${start.getFullYear()}-${String(start.getMonth() + 1).padStart(2, "0")}-01`,
    label: `${start.getFullYear()} bis ${ende}`,
  };
}

/** Die Zeile einer Note in der Liste, mit ihrem Kommentar daneben. */
export function notenZeile(grade: { grade_value: number; kind: GradeKind; weight: number; comment: string | null }): string {
  const gewicht = gewichtText(grade.weight);
  return [notenText(grade.grade_value), GRADE_ART[grade.kind], gewicht, grade.comment].filter(Boolean).join(" · ");
}

/** Die Zaehlung in der Kopfzeile der Karte. */
export function kopfZahl(summary: GradeSummary): string {
  if (summary.total_count === 0) {
    return "Noch keine Noten";
  }
  return `${notenAnzahl(summary.total_count)} · Schnitt ${notenText(summary.overall.average)}`;
}
