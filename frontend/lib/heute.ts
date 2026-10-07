import type { DashboardData } from "./types";

/*
 * Der Tagesueberblick ueber dem Assistenten auf der Startseite und die
 * Vorschlaege, was der Assistent als Naechstes tun kann.
 *
 * Reine Funktionen ohne I/O: die Startseite soll in einem Blick sagen, was
 * heute ansteht, und dann den Assistenten machen lassen - statt dass man sich
 * durch Karten klickt. Die Vorschlaege sind Saetze, die der Assistent ohne
 * Rueckfrage selbst ausfuehrt (siehe `backend/app/services/agent.py`).
 */

export interface HeutePunkt {
  /** Kurze Ueberschrift, z. B. "Jetzt" oder "Hausaufgaben". */
  label: string;
  text: string;
}

const TAG_MS = 24 * 60 * 60 * 1000;

/** Das Backend schreibt "Unbekannt", wenn kein Fach gesetzt ist - das ist kein Name. */
const PLATZHALTER_FAECHER = new Set(["unbekannt", "unbekanntes fach"]);

export function fachName(name: string | null | undefined): string {
  const text = (name ?? "").trim();
  return PLATZHALTER_FAECHER.has(text.toLowerCase()) ? "" : text;
}

function tageBis(ziel: Date, jetzt: Date): number {
  const a = new Date(jetzt.getFullYear(), jetzt.getMonth(), jetzt.getDate()).getTime();
  const b = new Date(ziel.getFullYear(), ziel.getMonth(), ziel.getDate()).getTime();
  return Math.round((b - a) / TAG_MS);
}

/** "heute", "morgen", "in 3 Tagen", "seit 2 Tagen". */
export function wannText(ziel: Date, jetzt: Date): string {
  const tage = tageBis(ziel, jetzt);
  if (tage === 0) return "heute";
  if (tage === 1) return "morgen";
  if (tage === -1) return "seit gestern";
  if (tage < 0) return `seit ${-tage} Tagen`;
  return `in ${tage} Tagen`;
}

function uhrzeit(value: Date): string {
  return new Intl.DateTimeFormat("de-DE", { hour: "2-digit", minute: "2-digit" }).format(value);
}

/** Hoechstens drei Punkte: Unterricht, Hausaufgaben, naechste Pruefung. */
export function heutePunkte(data: DashboardData, jetzt: Date): HeutePunkt[] {
  const punkte: HeutePunkt[] = [];

  const stunden = data.lessons
    .filter((lesson) => !lesson.isCancelled)
    .map((lesson) => ({ lesson, start: new Date(lesson.startTime), ende: new Date(lesson.endTime) }))
    .filter(({ ende }) => tageBis(ende, jetzt) === 0 && ende > jetzt)
    .sort((a, b) => a.start.getTime() - b.start.getTime());
  const naechste = stunden[0];
  if (naechste) {
    const laeuft = naechste.start <= jetzt;
    const wann = laeuft ? `bis ${uhrzeit(naechste.ende)}` : `um ${uhrzeit(naechste.start)}`;
    punkte.push({
      label: laeuft ? "Jetzt" : "Als Nächstes",
      text: `${naechste.lesson.subject} ${wann}${naechste.lesson.room ? ` · ${naechste.lesson.room}` : ""}`,
    });
  } else {
    punkte.push({ label: "Unterricht", text: "Heute keine Stunde mehr" });
  }

  const offen = data.homework
    .filter((item) => !item.completed)
    .map((item) => ({ item, faellig: new Date(item.dueDate) }))
    .sort((a, b) => a.faellig.getTime() - b.faellig.getTime());
  if (offen.length > 0) {
    const { item, faellig } = offen[0];
    const rest = offen.length > 1 ? ` (+${offen.length - 1} weitere)` : "";
    const fach = fachName(item.subject);
    punkte.push({ label: "Hausaufgaben", text: `${fach ? `${fach}: ` : ""}${item.title}, fällig ${wannText(faellig, jetzt)}${rest}` });
  } else {
    punkte.push({ label: "Hausaufgaben", text: "Alles erledigt" });
  }

  const pruefung = data.upcomingExams
    .map((exam) => ({ exam, datum: new Date(exam.date) }))
    .filter(({ datum }) => tageBis(datum, jetzt) >= 0)
    .sort((a, b) => a.datum.getTime() - b.datum.getTime())[0];
  if (pruefung) {
    punkte.push({ label: "Prüfung", text: `${fachName(pruefung.exam.subject) || pruefung.exam.title} ${wannText(pruefung.datum, jetzt)}` });
  }
  return punkte;
}

/**
 * Was der Assistent als Naechstes fuer dich tun kann - aus den Daten, nicht
 * erfunden. Hoechstens vier, das Wichtigste zuerst.
 */
export function agentVorschlaege(data: DashboardData | null, jetzt: Date): string[] {
  if (!data) {
    return ["Was steht diese Woche an?", "Plan meine Woche", "Trag die Mathe-Klausur nächsten Freitag ein"];
  }
  const vorschlaege: string[] = [];
  const offen = data.homework.filter((item) => !item.completed);
  const baldPruefung = data.upcomingExams.find((exam) => {
    const tage = tageBis(new Date(exam.date), jetzt);
    return tage >= 0 && tage <= 14;
  });
  if (offen.length > 0 || baldPruefung) {
    vorschlaege.push("Plan meine Woche");
  }
  const pruefungsFach = fachName(baldPruefung?.subject);
  if (pruefungsFach) {
    vorschlaege.push(`Frag mich ab für ${pruefungsFach}`);
  }
  const ersteOffen = [...offen].sort((a, b) => new Date(a.dueDate).getTime() - new Date(b.dueDate).getTime())[0];
  if (ersteOffen) {
    vorschlaege.push(`${fachName(ersteOffen.subject) || ersteOffen.title} erledigt`);
  }
  vorschlaege.push("Was steht diese Woche an?");
  if (vorschlaege.length < 4) {
    vorschlaege.push("Trag die Mathe-Klausur nächsten Freitag ein");
  }
  return vorschlaege.slice(0, 4);
}
