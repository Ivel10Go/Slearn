/**
 * Die deutschen Texte und Balken der Belohnungskarte.
 *
 * Alles hier ist rein: kein `fetch`, kein `Date.now()`, kein React. Das ist der Grund,
 * warum diese Datei ueberhaupt existiert - die Karte rechnet ihre Beschriftung in
 * einem Test nach, statt sie im Bildschirm zu pruefen. Bei dieser Karte ist das
 * wichtiger als bei den anderen, weil sie eine **Regel** erklaert: eine Oberflaeche,
 * die die Punkte selbst zaehlt, waere die zweite Wahrheit ueber die Belohnung.
 *
 * Zwei Grundsaetze fuer den Ton, und beide sind Absicht:
 *
 * * **Kein Dringlichkeitswort.** Es gibt keine "Stufen", keine "Level" und kein
 *   "noch so viel bis zum naechsten Level". Ein Punktezaehler, der den naechsten
 *   Meilenstein verspricht, produziert Druck - und Druck produziert Zaehlen statt
 *   Lernen.
 * * **Die Obergrenze wird genannt.** "15 von 80 Punkten" ist eine Regel. "15
 *   Punkte" neben einer Zahl, die nicht weiterwaechst, waere eine Entaeuschung.
 */

import { formatDate } from "./format";
import type { RewardBadge, RewardEntry, RewardOverview, RewardRules, RewardStep, RewardSummary } from "./types";

/** Wie viele Buchungen die Karte in der Liste zeigt.
 *
 * Bewusst eine Zahl in dieser Datei und nicht zwei: eine im Hook und eine in der
 * Karte waeren zwei Orte, an denen sich die Liste der Buchungen unterscheiden
 * koennten. Das Backend begrenzt die Antwort zusaetzlich - `ENTRY_LIMIT_MAX` dort -
 * , aber die Auswahl der sichtbaren Zeilen gehoert zur Anzeige, und die gehoert
 * neben die anderen Anzeige-Entscheidungen.
 */
export const LISTEN_LIMIT = 8;

/**
 * Punkte mit der Tagesobergrenze als Text: "15 von 80 Punkten".
 *
 * Genau diese beiden Zahlen sind der Grund, warum die Karte die Regel nennt: die
 * Menge ist gedeckelt, und eine Zahl, die nicht weiterwaechst, sieht ohne ihre
 * Obergrenze nach einem Fehler aus.
 */
export function punkteLabel(summary: Pick<RewardSummary, "today_points" | "today_cap">): string {
  return `${summary.today_points} von ${summary.today_cap} Punkten`;
}

/** Der Anteil eines Balkens in Prozent, begrenzt auf 0 bis 100. */
export function balkenAnteil(wert: number, maximum: number): number {
  if (maximum <= 0 || wert <= 0) {
    return 0;
  }
  return Math.min(100, Math.max(0, Math.round((wert / maximum) * 100)));
}

/** Der Fortschritt einer Auszeichnung als Anteil ihres Ziels. */
export function badgeAnteil(badge: Pick<RewardBadge, "progress" | "target">): number {
  return balkenAnteil(badge.progress, badge.target);
}

/**
 * Woran eine Auszeichnung haengt, in deutschen Worten.
 *
 * Ein `scope` wie `week:2026-W40` ist ein Schluessel fuer die Datenbank, kein Text
 * fuer Menschen. Die Karte nennt stattdessen das, was man wiedererkennt: das Fach,
 * das Thema, die Kalenderwoche - oder "Gesamt", wenn die Auszeichnung an nichts
 * haengt.
 */
export function badgeBereichLabel(badge: Pick<RewardBadge, "scope_kind" | "scope" | "subject_name" | "topic_name">): string {
  switch (badge.scope_kind) {
    case "subject":
      return badge.subject_name ?? "Fach";
    case "topic":
      return badge.topic_name ?? "Thema";
    case "week": {
      const treffer = /^week:(\d{4})-W(\d{1,2})$/.exec(badge.scope);
      return treffer ? `KW ${Number(treffer[2])} · ${treffer[1]}` : "Kalenderwoche";
    }
    default:
      return "Gesamt";
  }
}

/**
 * Der Fortschritt einer Auszeichnung in einem Satz.
 *
 * "2 von 5 Tagen" und nicht "60 %": die Einheit kommt aus dem Katalog, damit die
 * Karte nicht je nach Bedingung eine eigene Formulierung fuehren muss - und damit
 * die deutsche Grammatik nicht erraten werden muss. `unit_plural` ist die Dativform,
 * die der Satz braucht ("Tag" -> "Tagen"); ein Frontend, das sie selbst bildet,
 * wuerde "2 von 5 Tage" schreiben und stuende damit im Widerspruch zum Backend.
 *
 * Eine erreichte Auszeichnung nennt ihr Ziel und nicht mehr ihren Fortschritt: sie
 * ist ein vergangener Zustand, kein laufender Auftrag.
 */
export function badgeFortschrittLabel(badge: Pick<RewardBadge, "progress" | "target" | "unit" | "unit_plural" | "awarded">): string {
  const ziel = badge.target === 1 ? `1 ${badge.unit}` : `${badge.target} ${badge.unit_plural}`;
  if (badge.awarded) {
    return `Erreicht · ${ziel}`;
  }
  return `${badge.progress} von ${ziel}`;
}

/**
 * Die Punktregel in einem Absatz, erzeugt aus den Zahlen des Backends.
 *
 * Bewusst erzeugt und nicht festgetippt: die Erklaerung soll die Welle sein, nach der
 * das Backend auch rechnet. Ein hartcodierter Text waere die zweite Wahrheit, an der
 * die beiden auseinanderlaufen koennten - und eine stille Korrektur der Punkte waere
 * dann nicht mehr aufspuerbar.
 *
 * Der letzte Satz ist keine Behauptung ueber diesen Text, sondern der Vertrag, auf
 * den sich der Stack geeinigt hat: Punkte sind eine Anerkennung und gehen in keine
 * Note, in keine Prioritaet und in keinen KI-Kontext ein.
 */
export function regelSatz(regeln: RewardRules, stufen: RewardStep[]): string {
  const treppe = stufen.map((stufe) => `${stufe.minutes} Min. ${stufe.points} P.`).join(", ");
  return (
    `Je erledigte Aufgabe mit Thema ${regeln.task_points} Punkte. Je Fachtag eine Treppe: ${treppe}, ` +
    `höchstens ${regeln.subject_day_cap} Punkte pro Fach und Tag. Am Tag gibt es insgesamt höchstens ${regeln.day_cap} Punkte; ` +
    `danach wird nichts mehr ausgezahlt. Unter ${regeln.min_subject_day_minutes} Minuten zählt ein Fachtag nicht, ` +
    `und eine Aufgabe ohne Thema zahlt nie. Punkte sind eine Anerkennung: sie gehen nicht in Noten, ` +
    `nicht in die Priorität einer Aufgabe und nicht an den KI-Dienst.`
  );
}

/** Die Art einer Buchung in deutschen Worten. */
export function entryArtLabel(entry: Pick<RewardEntry, "kind">): string {
  return entry.kind === "task_completed" ? "Aufgabe" : "Fachtag";
}

/** Die Punkte einer Buchung als Text, mit korrekter Einzahl. */
export function entryPunkteLabel(points: number): string {
  return `${points} ${points === 1 ? "Punkt" : "Punkte"}`;
}

/** Das Datum einer Buchung als deutscher Text, ohne den Uhrzeit-Anteil. */
export function entryDatumLabel(earnedOn: string): string {
  return formatDate(new Date(`${earnedOn}T12:00:00`));
}

/**
 * Was eine Buchung mit 0 Punkten bedeutet - in einem Satz.
 *
 * Der Fall ist nicht theoretisch: ist das Tageskonto gefuellt, wird weiter gebucht,
 * aber nicht mehr ausgezahlt. Ohne diesen Hinweis stuende eine Zeile ohne Punkte da,
 * und die Karte wuerde nach einem Fehler aussehen.
 */
export function nullPunktHinweis(regeln: RewardRules): string {
  return `0 Punkte heißt: an diesem Tag gelernt, aber das Tageskonto war bereits bei ${regeln.day_cap} Punkten.`;
}

/**
 * Die Auszeichnungen, sortiert fuer die Anzeige.
 *
 * Erreichte zuerst, danach die mit dem groessten Fortschritt. Der Anteil ist eine
 * Rechnung und gehoert deshalb hierher und nicht in ein eigenes Sortieren in der
 * Karte - sonst wuerde die Reihenfolge an zwei Stellen festgelegt.
 */
export function badgeReihenfolge(badges: RewardBadge[]): RewardBadge[] {
  return [...badges].sort((links, rechts) => {
    if (links.awarded !== rechts.awarded) {
      return links.awarded ? -1 : 1;
    }
    const vergleich = badgeAnteil(rechts) - badgeAnteil(links);
    return vergleich !== 0 ? vergleich : links.code.localeCompare(rechts.code, "de");
  });
}

/** Die naechsten offenen Auszeichnungen, fuer die Liste unter den Kennzahlen. */
export function offeneBadges(badges: RewardBadge[], grenze = 4): RewardBadge[] {
  return badgeReihenfolge(badges.filter((badge) => !badge.awarded)).slice(0, grenze);
}

/**
 * Die Auszeichnungen in zwei Haufen: verdient und offen.
 *
 * Der Unterschied zu einem einzigen Zaehler ist der Unterschied zwischen "3 von 6
 * erreicht" und "3 erreicht". Die Zeilen zaehlen nur, was tatsaechlich errungen
 * wurde; eine Auszeichnung, die es noch nicht gibt, gehoert nicht in diese Zahl.
 */
export function badgeHaufen(
  badges: RewardBadge[],
): { erreicht: RewardBadge[]; offen: RewardBadge[] } {
  const sortiert = badgeReihenfolge(badges);
  return { erreicht: sortiert.filter((badge) => badge.awarded), offen: sortiert.filter((badge) => !badge.awarded) };
}

/** Die Bilanz der Auszeichnungen: was errungen wurde, und was es überhaupt gibt. */
export function badgeBilanz(overview: Pick<RewardOverview, "catalog" | "badges">): { erreicht: number; moeglich: number } {
  return { erreicht: overview.badges.filter((badge) => badge.awarded).length, moeglich: overview.catalog.length };
}

/** Die Punkte der Woche als Text, mit dem Tag, an dem die Woche endet. */
export function wochenLabel(summary: Pick<RewardSummary, "week_points" | "week_ends_on">): string {
  return `${summary.week_points} Punkte · Woche bis ${formatDate(new Date(`${summary.week_ends_on}T12:00:00`))}`;
}
