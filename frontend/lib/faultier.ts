import type { GespraechsPhase } from "./use-conversation";
import { t } from "./i18n";

/*
 * Was das Faultier gerade zeigt. Die Figur selbst (`components/assistant/faultier.tsx`)
 * kennt nur diesen Zustand; woraus er entsteht, steht hier, damit es ohne DOM
 * testbar ist.
 */

export type FaultierZustand = "ruhe" | "zuhoeren" | "denken" | "sprechen" | "freut" | "schlaeft";

export const FAULTIER_TEXT: Record<FaultierZustand, string> = {
  ruhe: t("Bereit"),
  zuhoeren: t("Hört zu"),
  denken: t("Denkt nach …"),
  sprechen: t("Spricht"),
  freut: t("Eingetragen"),
  schlaeft: t("Kein Modell verbunden"),
};

export interface FaultierSignale {
  /** Phase des Gespraechsmodus; `aus`, wenn er nicht laeuft. */
  phase: GespraechsPhase;
  /** Eine Anfrage an das Modell laeuft. */
  sendet: boolean;
  /** Eine Antwort wird gerade vorgelesen. */
  spielt: boolean;
  /** Halten zum Sprechen: das Mikrofon nimmt auf oder die Aufnahme wird erkannt. */
  nimmtAuf: boolean;
  /** Gerade wurde ein Vorschlag eingetragen (kurzer Moment). */
  freut: boolean;
  /** Es gibt keinen KI-Weg - der Assistent antwortet nur aus den Daten. */
  ohneModell?: boolean;
}

/**
 * Der Gespraechsmodus hat Vorrang, weil er seinen Zustand selbst fuehrt; danach
 * gilt, was der Nutzer gerade tut (sprechen), dann was der Assistent tut.
 */
export function faultierZustand(signale: FaultierSignale): FaultierZustand {
  switch (signale.phase) {
    case "startet":
    case "hoert":
    case "nimmt_auf":
      return "zuhoeren";
    case "erkennt":
    case "denkt":
      return "denken";
    case "antwortet":
      return "sprechen";
    case "aus":
      break;
  }
  if (signale.nimmtAuf) {
    return "zuhoeren";
  }
  if (signale.sendet) {
    return "denken";
  }
  if (signale.spielt) {
    return "sprechen";
  }
  if (signale.freut) {
    return "freut";
  }
  return signale.ohneModell ? "schlaeft" : "ruhe";
}
