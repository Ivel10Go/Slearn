/**
 * Die deutschen Texte und Balken der Fortschrittskarte.
 *
 * Alles hier ist rein: kein `fetch`, kein `Date.now()`, kein React. Das ist der
 * Grund, warum diese Datei ueberhaupt existiert - die Karten rechnen ihre
 * Beschriftung in einem Test nach, statt sie im Bildschirm zu pruefen. Die
 * Anzeigetexte sind damit von den API-Feldern getrennt: `confidence_label` und
 * `interval_days` kommen vom Backend, die Wochentagsnamen fuer die Balken
 * entstehen aus dessen `label`.
 *
 * Die Zeitangaben kommen **aus** der Antwort und werden nicht nachgerechnet.
 * Das ist Absicht: das Backend rechnet in der Zeitzone des Nutzers, der
 * Browser in seiner eigenen, und ein zweiter Rechenschritt im Client waere
 * genau die Falle, die die Serie sonst um einen Tag verschiebt.
 */

import { formatDuration } from "./format";
import type { ConfidenceStep, LearningInsights, Streak, TopicProgress, WeeklyGoal, WeekdayStudyTime } from "./types";
import { t, tn, translateServerText } from "./i18n";

/**
 * Lernzeit als Text.
 *
 * `formatDuration(0)` sagt "Ohne Dauer" - das stimmt fuer eine Aufgabe, fuer
 * ein Protokoll aber nicht: null Minuten gelernt ist ein Ergebnis, keine
 * fehlende Angabe.
 */
export function lernzeit(minutes: number): string {
  return minutes > 0 ? formatDuration(minutes) : t("{n} Min.", { n: 0 });
}

/** Der Anteil eines Balkens in Prozent, begrenzt auf 0 bis 100. */
export function balkenAnteil(wert: number, maximum: number): number {
  if (maximum <= 0 || wert <= 0) {
    return 0;
  }
  return Math.min(100, Math.max(0, Math.round((wert / maximum) * 100)));
}

/** Zwei Buchstaben aus einem Wochentagsnamen: "Montag" wird zu "Mo". */
export function weekdayKurz(label: string): string {
  return label.slice(0, 2);
}

/** Eine Zeile je Wochentag, auf 100 Prozent des groessten Werts bezogen. */
export function weekdayBalken(wochentage: WeekdayStudyTime[]): Array<WeekdayStudyTime & { anteil: number; maximum: number }> {
  const maximum = wochentage.reduce((groesster, eintrag) => Math.max(groesster, eintrag.minutes), 0);
  return wochentage.map((eintrag) => ({ ...eintrag, anteil: balkenAnteil(eintrag.minutes, maximum), maximum }));
}

/**
 * Wann ein Thema wieder an der Reihe ist.
 *
 * `days_until_review` kommt vom Backend und ist negativ, wenn der Termin
 * verstrichen ist. Negative und positive Faelle werden getrennt behandelt:
 * "vor 3 Tagen" waere eine Zusage, die nicht stimmt, weil der Termin
 * erledigt sein kann, ohne dass jemand etwas getan hat.
 */
export function wiederholungLabel(thema: Pick<TopicProgress, "next_review_on" | "days_until_review" | "is_due">): string {
  if (thema.next_review_on === null) {
    return t("Noch nie wiederholt");
  }
  const tage = thema.days_until_review;
  if (tage === null) {
    return t("Wiederholen am {datum}", { datum: thema.next_review_on });
  }
  if (tage === 0) {
    return t("Heute wiederholen");
  }
  if (tage === 1) {
    return t("Morgen wiederholen");
  }
  if (tage === -1) {
    return t("Seit gestern fällig");
  }
  if (tage < 0) {
    return t("Seit {n} Tagen fällig", { n: Math.abs(tage) });
  }
  return t("In {n} Tagen", { n: tage });
}

/**
 * Die Serie in einem Satz.
 *
 * "Heute noch offen" ist der Unterschied zwischen einer Serie, die es noch
 * gibt, und einer, die man schon verloren hat: wer morgens nachsieht, hat den
 * Tag noch vor sich.
 */
export function streakLabel(serie: Pick<Streak, "current_days" | "studied_today">): string {
  const tage = serie.current_days;
  if (tage <= 0) {
    return t("Keine laufende Serie");
  }
  if (tage === 1) {
    return serie.studied_today ? t("1 Tag in Folge") : t("1 Tag · heute noch offen");
  }
  return serie.studied_today ? t("{n} Tage in Folge", { n: tage }) : t("{n} Tage · heute noch offen", { n: tage });
}

/** "180 von 300 Min." - der Wochenstand mit seinem Ziel, ohne Prozentzahl. */
export function wochenzielLabel(woche: Pick<WeeklyGoal, "studied_minutes" | "goal_minutes">): string {
  return t("{ist} von {ziel}", { ist: lernzeit(woche.studied_minutes), ziel: lernzeit(woche.goal_minutes) });
}

/**
 * Die Regel in einem Satz, aus der Stufentabelle des Backends.
 *
 * Bewusst erzeugt und nicht festgetippt: die Erklaerung soll die Waelle sein,
 * nach der das Backend auch rechnet. Ein hartcodierter Text waere die zweite
 * Wahrheit, an der sie auseinanderlaufen koennte.
 */
export function intervallRegel(stufen: ConfidenceStep[]): string {
  if (stufen.length === 0) {
    return "";
  }
  const teile = stufen.map((stufe) => `${translateServerText(stufe.label)} ${tn(stufe.interval_days, "{n} Tag", "{n} Tage")}`);
  return t("Fünf Stufen, ein festes Intervall je Stufe: {liste}. Je sicherer die Einschätzung, desto länger bis zur nächsten Wiederholung.", { liste: teile.join(", ") });
}

/**
 * Die Themen, deren Wiederholung heute oder ueberfaellig ist.
 *
 * Die Reihenfolge kommt vom Backend (`is_due` zuerst, dann der Termin); hier
 * wird nur gefiltert. Ein eigenes Sortieren waere eine zweite Wahrheit ueber
 * dieselbe Frage.
 */
export function faelligeThemen(themen: TopicProgress[]): TopicProgress[] {
  return themen.filter((thema) => thema.is_due);
}

/** Wie viele der gelisteten Themen noch nie eingeschaetzt wurden. */
export function unBewerteteAnzahl(themen: TopicProgress[]): number {
  return themen.filter((thema) => thema.last_reviewed_at === null).length;
}

/**
 * Der Hinweis auf eine noch laufende Lernsession.
 *
 * Offene Sessions stehen bewusst ausserhalb aller Summen. Deshalb wird hier
 * ausdruecklich darauf hingewiesen, statt sie in die Zahl der Minuten zu
 * rechnen, die dann nach dem Beenden springen wuerde.
 */
export function laufendeSessionHinweis(insights: Pick<LearningInsights, "open_sessions" | "active_minutes">): string | null {
  if (insights.open_sessions <= 0) {
    return null;
  }
  const anzahl = tn(insights.open_sessions, "{n} Lernsession läuft noch", "{n} Lernsessions laufen noch");
  return t("{anzahl} · {zeit} zählen erst nach dem Beenden.", { anzahl, zeit: lernzeit(insights.active_minutes) });
}
