/**
 * Bruecke zwischen ``<input type="datetime-local">`` und der API.
 *
 * Das Backend haelt alle Zeitpunkte als UTC mit Offset (`UTCResponseModel`).
 * Ein ``datetime-local``-Feld kennt dagegen keine Zeitzone und sendet
 * ``2026-03-04T07:45`` - was der Browser als **lokale** Zeit liest. Ohne diese
 * Umrechnung verschiebt sich jede Formular-Eingabe um die Differenz zwischen
 * der Zeitzone des Browsers und der des Lernenden.
 *
 * Deshalb: Eingaben immer ueber `toUtcIso` hinaus, Anzeigen immer ueber
 * `toDateTimeLocal` hinein. Beide Funktionen sind bewusst synchron und
 * arbeiten auf einem uebergebenen `Date`, damit sie im Test deterministisch
 * bleiben.
 */

/** Millisekunden je Minute - fuer die Umrechnung der Zeitverschiebung. */
const MINUTE_MS = 60_000;

/**
 * `YYYY-MM-DDTHH:mm` in lokaler Zeit, wie es ein `datetime-local`-Feld braucht.
 *
 * minutesgenau: Sekunden wuerden in der Anzeige des Feldes als Text landen,
 * weil das Eingabefeld sie nicht kennt und beim Speichern auf `00` zurueckfallen.
 */
export function toDateTimeLocal(value: string | Date | null | undefined): string {
  const date = value instanceof Date ? value : value ? new Date(value) : null;
  if (!date || Number.isNaN(date.getTime())) {
    return "";
  }
  // `Date#toISOString` rechnet in UTC. Um die lokale Wandzeit zu bekommen,
  // zieht man die eigene Verschiebung ab und liest das Ergebnis wieder als UTC.
  const shifted = new Date(date.getTime() - date.getTimezoneOffset() * MINUTE_MS);
  return shifted.toISOString().slice(0, 16);
}

/** Der lokale Tag als ``YYYY-MM-DD``, wie ihn ein `date`-Feld erwartet. */
export function toDateInput(value: string | Date | null | undefined): string {
  return toDateTimeLocal(value).slice(0, 10);
}

/**
 * Ein Formularfeld nach ISO-8601 mit Zonenangabe.
 *
 * Leere oder unbrauchbare Eingaben ergeben `null`, nicht `Invalid Date`:
 * die Aufrufer entscheiden dann selbst, ob das Feld Pflicht war. Ein
 * `new Date("").toISOString()` wuerde hier werfen und die ganze Formularpruefung
 * abbrechen statt eine Meldung zu zeigen.
 */
export function toUtcIso(value: string | null | undefined): string | null {
  const trimmed = value?.trim();
  if (!trimmed) {
    return null;
  }
  const date = new Date(trimmed);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

/**
 * Kombiniert ein Datum und eine Uhrzeit zu einem Zeitpunkt.
 *
 * `datetime-local` reicht als Eingabeformat; `date` braucht aber einen
 * Zeitanteil, sonst landet es um 00:00 Uhr mitten in der Nacht, wenn der
 * Lernende nur den Tag waehlt.
 */
export function toUtcIsoFromParts(date: string | null | undefined, time?: string | null): string | null {
  const trimmedDate = date?.trim();
  if (!trimmedDate) {
    return null;
  }
  const trimmedTime = time?.trim();
  // `"2026-03-04T00:00"` traegt die Zeitzone des Browsers, `"2026-03-04"` nicht.
  return toUtcIso(trimmedTime ? `${trimmedDate}T${trimmedTime.length === 5 ? `${trimmedTime}:00` : trimmedTime}` : `${trimmedDate}T00:00`);
}

/** Wie `toUtcIsoFromParts`, aber mit einem fest vorgegebenen Zeitanteil. */
export function toUtcIsoAtTime(value: string | Date | null | undefined, time: string): string | null {
  const day = toDateInput(value);
  return day ? toUtcIsoFromParts(day, time) : null;
}
