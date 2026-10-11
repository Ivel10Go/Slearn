"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import Link from "next/link";
import { AudioLines, Check, CornerDownLeft, LoaderCircle, Mic, PhoneOff, Plus, RotateCcw, Square, ThumbsDown, ThumbsUp, Trash2, Volume2, VolumeX, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { AbfrageCard, LernprofilCard } from "@/components/assistant/learning-cards";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { Faultier } from "@/components/assistant/faultier";
import { describeSource } from "@/lib/ai-runtime";
import { ApiError } from "@/lib/api";
import {
  ACTION_LABELS,
  addAssistantMemory,
  applyAssistantProposal,
  bestaetigung,
  clearHistory,
  deleteAssistantMemory,
  describeProposalTime,
  feedbackNebenbei,
  getAssistantMemories,
  getAssistantRoutes,
  historyForRequest,
  loadHistory,
  parseRouteKey,
  routeKey,
  saveHistory,
  sendAssistantMessage,
  type AssistantMemory,
  type AssistantProposal,
  type AssistantReply,
  type AssistantRoute,
  type ChatMessage,
} from "@/lib/assistant";
import { FAULTIER_TEXT, faultierZustand, type FaultierZustand } from "@/lib/faultier";
import { beschreibeAufnahme } from "@/lib/speech";
import { PHASEN_TEXT, useConversation } from "@/lib/use-conversation";
import { useSpeech } from "@/lib/use-speech";
import { cn } from "@/lib/utils";
import { t, tSplit } from "@/lib/i18n";

/*
 * Der Assistent: ein Gespraech mit dem gewaehlten Modell ueber Unterlagen,
 * Stundenplan, Pruefungen und Hausaufgaben.
 *
 * - **Vorschlaege statt stiller Eintraege.** Bittet man ihn, etwas einzutragen,
 *   erscheint eine Karte mit "Eintragen" - das Modell kann sich bei
 *   "naechsten Donnerstag" verrechnen, und das sieht man so vorher.
 * - **Sprechen.** Halten zum Sprechen schickt das Transkript direkt ab. Mit
 *   "Antworten vorlesen" liest die lokale Stimme jede neue Antwort vor; das
 *   Einschalten ist die Nutzergeste, die der Browser fuer die Wiedergabe will.
 * - **Gedaechtnis.** Was der Assistent sich merkt, steht rechts und laesst sich
 *   loeschen. Der Verlauf selbst liegt nur in diesem Browser.
 * - **Faultier.** Das Maskottchen ist das Gesicht des Assistenten: gross in
 *   der Seitenleiste (bzw. im leeren Chat auf schmalen Bildschirmen), als Kopf
 *   neben jeder Antwort. Es zeigt, was gerade passiert - zuhoeren, nachdenken,
 *   sprechen, sich ueber einen Eintrag freuen (`lib/faultier.ts`).
 */

type ProposalState = "offen" | "laeuft" | "eingetragen" | "verworfen";

interface ViewMessage extends ChatMessage {
  id: number;
  reply?: AssistantReply;
  proposalStates?: ProposalState[];
  proposalNotes?: (string | null)[];
  /** Der Daumen zu dieser Antwort - nur mit Trainingsfreigabe sichtbar. */
  bewertung?: "good" | "bad" | null;
}

const AUTO = "auto";
const VORLESEN_KEY = "lernraum-assistent-vorlesen";
/** So lange freut sich das Faultier nach einem Eintrag. */
const FREUDE_MS = 2500;

function fehlertext(ursache: unknown, ersatz: string) {
  return ursache instanceof ApiError && ursache.message ? ursache.message : ersatz;
}

let naechsteId = 1;
function mitId(message: ChatMessage, extra: Partial<ViewMessage> = {}): ViewMessage {
  naechsteId += 1;
  return { ...message, id: naechsteId, ...extra };
}

function nurText(messages: ViewMessage[]): ChatMessage[] {
  return messages.map(({ role, content }) => ({ role, content }));
}

export function AssistantView() {
  const [profilStand, setProfilStand] = useState(0);
  const [messages, setMessages] = useState<ViewMessage[]>([]);
  const [eingabe, setEingabe] = useState("");
  const [sendet, setSendet] = useState(false);
  const [fehler, setFehler] = useState<string | null>(null);
  const [routes, setRoutes] = useState<AssistantRoute[] | null>(null);
  const [freut, setFreut] = useState(false);
  const freudeTimer = useRef<number | null>(null);
  const [auswahl, setAuswahl] = useState<string>(AUTO);
  const [vorlesen, setVorlesen] = useState(false);
  const sprache = useSpeech();
  const listenEnde = useRef<HTMLDivElement | null>(null);
  const messagesRef = useRef<ViewMessage[]>([]);
  const sendenRef = useRef<((text: string) => Promise<string | null>) | null>(null);

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  // Verlauf, Modelle und Vorlese-Einstellung nach dem ersten Rendern laden.
  useEffect(() => {
    const timer = window.setTimeout(() => {
      const geladen = loadHistory().map((message) => mitId(message));
      messagesRef.current = geladen;
      setMessages(geladen);
      try {
        setVorlesen(window.localStorage.getItem(VORLESEN_KEY) === "1");
      } catch {
        // ohne Speicher bleibt Vorlesen aus
      }
      // Eine Frage aus der Dashboard-Karte (`/assistent?frage=…`) wird einmal gestellt.
      const frage = new URLSearchParams(window.location.search).get("frage");
      if (frage?.trim()) {
        window.history.replaceState(null, "", window.location.pathname);
        void sendenRef.current?.(frage);
      }
    }, 0);
    getAssistantRoutes()
      .then((liste) => {
        setRoutes(liste);
        // Das zuletzt gewaehlte geladene Modell ist vorausgewaehlt.
        const gewaehlt = liste.find((route) => route.selected);
        if (gewaehlt) {
          setAuswahl((aktuell) => (aktuell === AUTO ? routeKey(gewaehlt) : aktuell));
        }
      })
      .catch(() => setRoutes([]));
    return () => window.clearTimeout(timer);
  }, []);

  useEffect(
    () => () => {
      if (freudeTimer.current !== null) {
        window.clearTimeout(freudeTimer.current);
      }
    },
    [],
  );

  const freuen = useCallback(() => {
    if (freudeTimer.current !== null) {
      window.clearTimeout(freudeTimer.current);
    }
    setFreut(true);
    freudeTimer.current = window.setTimeout(() => setFreut(false), FREUDE_MS);
  }, []);

  useEffect(() => {
    listenEnde.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, sendet]);

  const { kannAusgabe, sprechen } = sprache;
  /** Schickt eine Nachricht und liefert die Antwort - der Gespraechsmodus liest sie selbst vor. */
  const senden = useCallback(
    async (text: string, ausGespraech = false): Promise<string | null> => {
      const inhalt = text.trim();
      if (!inhalt || sendet) {
        return null;
      }
      const verlauf = [...messagesRef.current, mitId({ role: "user", content: inhalt })];
      setMessages(verlauf);
      saveHistory(nurText(verlauf));
      setEingabe("");

      // Im Gespraech bestaetigt ein gesprochenes "Ja" die offenen Vorschlaege der
      // letzten Antwort direkt - ohne Modellaufruf, die Antwort ist sofort da.
      const letzte = [...messagesRef.current].reverse().find((message) => message.role === "assistant");
      const offen = (letzte?.reply?.proposals ?? []).filter((_, index) => (letzte?.proposalStates?.[index] ?? "offen") === "offen");
      const entscheidung = ausGespraech && letzte && offen.length > 0 ? bestaetigung(inhalt) : null;
      if (letzte && entscheidung) {
        let text: string;
        const states = [...(letzte.proposalStates ?? [])];
        if (entscheidung === "ja") {
          const beispiel = letzte.reply?.training_example_id ?? null;
          const ergebnisse = await Promise.allSettled(offen.map((proposal) => applyAssistantProposal(proposal, beispiel)));
          const fehlgeschlagen = ergebnisse.filter((ergebnis) => ergebnis.status === "rejected").length;
          letzte.reply?.proposals.forEach((_, index) => {
            if ((states[index] ?? "offen") === "offen") {
              states[index] = "eingetragen";
            }
          });
          text = fehlgeschlagen ? t("Das hat leider nicht geklappt. Trag es bitte über den Knopf ein.") : t("Erledigt, ist eingetragen.");
          if (fehlgeschlagen) {
            letzte.reply?.proposals.forEach((_, index) => (states[index] = "offen"));
          } else {
            freuen();
          }
        } else {
          letzte.reply?.proposals.forEach((_, index) => {
            if ((states[index] ?? "offen") === "offen") {
              states[index] = "verworfen";
            }
          });
          feedbackNebenbei(letzte.reply?.training_example_id, "rejected");
          text = t("Okay, ich trage nichts ein.");
        }
        const aktualisiert = verlauf.map((message) => (message.id === letzte.id ? { ...message, proposalStates: states } : message));
        const fertig = [...aktualisiert, mitId({ role: "assistant", content: text })];
        setMessages(fertig);
        saveHistory(nurText(fertig));
        return text;
      }

      setSendet(true);
      setFehler(null);
      try {
        const antwort = await sendAssistantMessage(historyForRequest(nurText(verlauf)), parseRouteKey(auswahl), ausGespraech);
        const neu = mitId(
          { role: "assistant", content: antwort.reply },
          { reply: antwort, proposalStates: antwort.proposals.map(() => "offen"), proposalNotes: antwort.proposals.map(() => null) },
        );
        const fertig = [...verlauf, neu];
        setMessages(fertig);
        saveHistory(nurText(fertig));
        if (vorlesen && kannAusgabe && !ausGespraech) {
          sprechen(antwort.reply);
        }
        return antwort.reply;
      } catch (ursache) {
        setFehler(fehlertext(ursache, t("Der Assistent hat nicht geantwortet.")));
        return null;
      } finally {
        setSendet(false);
      }
    },
    [auswahl, sendet, kannAusgabe, sprechen, vorlesen, freuen],
  );

  useEffect(() => {
    sendenRef.current = senden;
  }, [senden]);

  const gespraech = useConversation({ onUtterance: (text) => senden(text, true) });
  const gespraechMoeglich = Boolean(sprache.faehigkeiten?.speech_to_text.available && sprache.faehigkeiten?.text_to_speech.available);

  // Ein fertiges Transkript wird sofort als Nachricht abgeschickt.
  const transkript = sprache.transkript.trim();
  const { aufnahmeZustand, abbrechen, uebernehmen } = sprache;
  useEffect(() => {
    if (!transkript || aufnahmeZustand !== "bereit") {
      return;
    }
    const timer = window.setTimeout(() => {
      // Erst das Transkript leeren, dann senden: `abbrechen()` laesst es
      // stehen, und sonst feuerte dieser Effekt bei jeder neuen
      // `senden`-Identitaet erneut - dieselbe Nachricht immer wieder.
      uebernehmen("");
      abbrechen();
      void senden(transkript);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [transkript, aufnahmeZustand, abbrechen, uebernehmen, senden]);

  function absenden(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void senden(eingabe);
  }

  function tastendruck(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void senden(eingabe);
    }
  }

  function neuesGespraech() {
    sprache.schweigen();
    clearHistory();
    setMessages([]);
    setFehler(null);
  }

  function vorlesenUmschalten() {
    const neu = !vorlesen;
    setVorlesen(neu);
    if (!neu) {
      sprache.schweigen();
    }
    try {
      window.localStorage.setItem(VORLESEN_KEY, neu ? "1" : "0");
    } catch {
      // nur eine Bequemlichkeit
    }
  }

  function vorschlagSetzen(messageId: number, index: number, state: ProposalState, note: string | null = null) {
    setMessages((aktuell) =>
      aktuell.map((message) => {
        if (message.id !== messageId || !message.proposalStates) {
          return message;
        }
        const states = [...message.proposalStates];
        const notes = [...(message.proposalNotes ?? states.map(() => null))];
        states[index] = state;
        notes[index] = note;
        return { ...message, proposalStates: states, proposalNotes: notes };
      }),
    );
  }

  async function eintragen(messageId: number, index: number, proposal: AssistantProposal, beispiel: number | null) {
    vorschlagSetzen(messageId, index, "laeuft");
    try {
      const ergebnis = await applyAssistantProposal(proposal, beispiel);
      vorschlagSetzen(messageId, index, "eingetragen", ergebnis.message);
      freuen();
    } catch (ursache) {
      vorschlagSetzen(messageId, index, "offen", fehlertext(ursache, t("Der Eintrag ist fehlgeschlagen.")));
    }
  }

  function verwerfen(message: ViewMessage, index: number) {
    vorschlagSetzen(message.id, index, "verworfen");
    feedbackNebenbei(message.reply?.training_example_id, "rejected");
  }

  /** Noch einmal derselbe Daumen nimmt ihn zurueck. */
  function bewerten(message: ViewMessage, wert: "good" | "bad") {
    const neu = message.bewertung === wert ? null : wert;
    setMessages((aktuell) => aktuell.map((m) => (m.id === message.id ? { ...m, bewertung: neu } : m)));
    feedbackNebenbei(message.reply?.training_example_id, neu ?? "cleared");
  }

  const laeuftAufnahme = sprache.aufnahmeZustand === "nimmt_auf";
  const wirdErkannt = sprache.aufnahmeZustand === "wird_gesendet";
  const ohneModell = routes !== null && routes.length === 0;
  const zustand = faultierZustand({
    phase: gespraech.phase,
    sendet,
    spielt: sprache.spielt,
    nimmtAuf: laeuftAufnahme || wirdErkannt,
    freut,
    ohneModell,
  });
  const letzteAntwortId = [...messages].reverse().find((message) => message.role === "assistant")?.id;

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[22px] font-semibold leading-8 tracking-tight">{t("Assistent")}</h1>
          <p className="mt-0.5 max-w-2xl text-[13px] leading-5 text-muted-foreground">
            {t("Frag zu deinen Unterlagen, plane deine Woche oder lass Prüfungen, Hausaufgaben und Termine eintragen. Tippen oder sprechen.")}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor="assistent-modell" className="sr-only">
            {t("Modell")}
          </label>
          <Select id="assistent-modell" value={auswahl} onChange={(event) => setAuswahl(event.target.value)} className="h-8 w-auto min-w-48">
            <option value={AUTO}>{t("Automatisch (lokal zuerst)")}</option>
            {(routes ?? []).map((route) => (
              <option key={routeKey(route)} value={routeKey(route)}>
                {route.local_model_id
                  ? t("{name} (auf diesem Rechner)", { name: route.name })
                  : `${route.name} · ${route.model} ${route.source === "local" ? t("(lokal)") : t("(online)")}`}
              </option>
            ))}
          </Select>
          <Button type="button" size="sm" variant="outline" onClick={neuesGespraech} disabled={messages.length === 0 && !fehler}>
            <RotateCcw aria-hidden="true" />
            {t("Neues Gespräch")}
          </Button>
        </div>
      </header>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_300px]">
        <Card className="flex min-h-[60vh] flex-col">
          <CardContent className="flex flex-1 flex-col gap-3 p-0">
            <div className="flex-1 space-y-4 overflow-y-auto px-4 py-4" aria-live="polite">
              {messages.length === 0 && !sendet ? <Leer ohneModell={ohneModell} zustand={zustand} onBeispiel={(text) => void senden(text)} /> : null}
              {messages.map((message) =>
                message.role === "user" ? (
                  <div key={message.id} className="flex justify-end">
                    <p className="max-w-[85%] whitespace-pre-wrap rounded-md bg-foreground px-3 py-2 text-[13px] leading-6 text-background">{message.content}</p>
                  </div>
                ) : (
                  <AssistentNachricht
                    key={message.id}
                    message={message}
                    zustand={message.id === letzteAntwortId && !sendet ? zustand : null}
                    kannVorlesen={sprache.kannAusgabe}
                    spielt={sprache.spielt}
                    onVorlesen={() => sprache.sprechen(message.content)}
                    onStopp={sprache.schweigen}
                    onEintragen={(index, proposal) => void eintragen(message.id, index, proposal, message.reply?.training_example_id ?? null)}
                    onVerwerfen={(index) => verwerfen(message, index)}
                    onBewerten={(wert) => bewerten(message, wert)}
                  />
                ),
              )}
              {sendet ? (
                <p className="flex items-center gap-2.5 text-[13px] text-muted-foreground" role="status">
                  <Faultier zustand="denken" groesse={28} />
                  {t("Denkt nach … (beim ersten Mal startet das Modell, das dauert ein paar Sekunden)")}
                </p>
              ) : null}
              <div ref={listenEnde} />
            </div>

            {fehler ? (
              <p className="mx-4 rounded-md border border-foreground/30 bg-muted px-3 py-2 text-[13px]" role="alert">
                {fehler}
              </p>
            ) : null}

            {gespraech.aktiv ? (
              <div className="mx-3 flex flex-wrap items-center gap-3 rounded-md border border-foreground/40 px-3 py-2" role="status" aria-live="polite">
                <Faultier zustand={zustand} groesse={28} />
                <span className="flex h-6 items-end gap-0.5" aria-hidden="true">
                  {[0.35, 0.7, 1, 0.7, 0.35].map((faktor, index) => (
                    <span
                      key={index}
                      className="w-1 rounded-sm bg-foreground transition-[height] duration-100"
                      style={{ height: `${Math.max(3, Math.round(gespraech.pegel * faktor * 24))}px` }}
                    />
                  ))}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-[13px] font-medium">{PHASEN_TEXT[gespraech.phase]}</p>
                  {gespraech.hinweis ? (
                    <p className="text-xs text-muted-foreground">{gespraech.hinweis}</p>
                  ) : gespraech.erkannt ? (
                    <p className="truncate text-xs text-muted-foreground">{t("Verstanden: „{text}“", { text: gespraech.erkannt })}</p>
                  ) : null}
                </div>
                {gespraech.phase === "antwortet" ? (
                  <Button type="button" size="sm" variant="outline" onClick={gespraech.unterbrechen}>
                    <Square aria-hidden="true" />
                    {t("Unterbrechen")}
                  </Button>
                ) : null}
                <Button type="button" size="sm" onClick={gespraech.beenden}>
                  <PhoneOff aria-hidden="true" />
                  {t("Gespräch beenden")}
                </Button>
              </div>
            ) : gespraech.hinweis ? (
              <p className="mx-3 text-xs text-muted-foreground" role="status">
                {gespraech.hinweis}
              </p>
            ) : null}

            <form onSubmit={absenden} className="space-y-2 border-t border-border p-3">
              <label htmlFor="assistent-eingabe" className="sr-only">
                {t("Nachricht an den Assistenten")}
              </label>
              <Textarea
                id="assistent-eingabe"
                rows={2}
                value={eingabe}
                onChange={(event) => setEingabe(event.target.value)}
                onKeyDown={tastendruck}
                placeholder={t("z. B. „Trag die Mathe-Klausur nächsten Freitag ein“ oder „Erklär mir den Zellzyklus aus meinem Skript“")}
                disabled={sendet}
                className="resize-none"
              />
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex flex-wrap items-center gap-2">
                  {!gespraech.aktiv ? (
                    <Button
                      type="button"
                      size="sm"
                      onClick={gespraech.starten}
                      disabled={!gespraechMoeglich || sendet}
                      title={gespraechMoeglich ? t("Freihändig reden: zuhören, antworten, vorlesen") : sprache.grundEingabe || sprache.grundAusgabe}
                    >
                      <AudioLines aria-hidden="true" />
                      {t("Gespräch starten")}
                    </Button>
                  ) : null}
                  <Button
                    type="button"
                    size="sm"
                    variant={laeuftAufnahme ? "default" : "outline"}
                    disabled={!sprache.kannEingabe || sendet || wirdErkannt || gespraech.aktiv}
                    aria-pressed={laeuftAufnahme}
                    onPointerDown={(event) => {
                      event.preventDefault();
                      sprache.aufnehmen();
                    }}
                    onPointerUp={sprache.beenden}
                    onPointerLeave={() => (laeuftAufnahme ? sprache.beenden() : undefined)}
                    onPointerCancel={() => (laeuftAufnahme ? sprache.beenden() : undefined)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        sprache.aufnehmen();
                      }
                    }}
                    onKeyUp={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        sprache.beenden();
                      }
                    }}
                    title={sprache.kannEingabe ? t("Gedrückt halten und sprechen") : sprache.grundEingabe}
                  >
                    {laeuftAufnahme ? <Square aria-hidden="true" /> : wirdErkannt ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <Mic aria-hidden="true" />}
                    {laeuftAufnahme ? t("Loslassen zum Senden") : wirdErkannt ? t("Wird erkannt …") : t("Halten zum Sprechen")}
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant={vorlesen ? "default" : "ghost"}
                    aria-pressed={vorlesen}
                    onClick={vorlesenUmschalten}
                    disabled={!sprache.kannAusgabe}
                    title={sprache.kannAusgabe ? t("Neue Antworten automatisch vorlesen") : sprache.grundAusgabe}
                  >
                    {vorlesen ? <Volume2 aria-hidden="true" /> : <VolumeX aria-hidden="true" />}
                    {t("Antworten vorlesen")}
                  </Button>
                  {laeuftAufnahme ? <span className="text-xs text-muted-foreground">{beschreibeAufnahme(sprache.aufnahmeZustand, sprache.sekunden)}</span> : null}
                </div>
                <Button type="submit" size="sm" disabled={sendet || !eingabe.trim()}>
                  {sendet ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <CornerDownLeft aria-hidden="true" />}
                  {t("Senden")}
                </Button>
              </div>
              {sprache.fehler ? (
                <p className="text-xs text-muted-foreground" role="status">
                  {sprache.fehler}
                </p>
              ) : null}
              {!sprache.kannEingabe || !sprache.kannAusgabe ? (
                <p className="text-[11px] leading-4 text-muted-foreground">
                  {!sprache.kannEingabe ? `${t("Sprechen: {grund}", { grund: sprache.grundEingabe || t("nicht verfügbar") })} ` : ""}
                  {!sprache.kannAusgabe ? `${t("Vorlesen: {grund}", { grund: sprache.grundAusgabe || t("nicht verfügbar") })} ` : ""}
                  {tSplit("Modelle und Stimmen richtest du unter {link} ein.")[0]}
                  <Link href="/ki" className="underline underline-offset-2 hover:text-foreground">
                    {t("KI & Modelle")}
                  </Link>
                  {tSplit("Modelle und Stimmen richtest du unter {link} ein.")[1]}
                </p>
              ) : null}
            </form>
          </CardContent>
        </Card>

        <div className="space-y-4 self-start">
          <div className="hidden flex-col items-center gap-1 pt-2 lg:flex">
            <Faultier zustand={zustand} groesse={132} koerper folgen beschriftung={`Faultier-Assistent: ${FAULTIER_TEXT[zustand]}`} />
            <p className="text-xs text-muted-foreground">{FAULTIER_TEXT[zustand]}</p>
          </div>
          <AbfrageCard onErgebnis={() => setProfilStand((stand) => stand + 1)} />
          <LernprofilCard refreshKey={profilStand} />
          <GedaechtnisCard messages={messages} />
        </div>
      </div>
    </div>
  );
}

function Leer({ ohneModell, zustand, onBeispiel }: { ohneModell: boolean; zustand: FaultierZustand; onBeispiel: (text: string) => void }) {
  const beispiele = [
    t("Was steht diese Woche an?"),
    t("Trag die Bio-Klausur am nächsten Donnerstag ein."),
    t("Fasse mein Skript zum Zellzyklus zusammen."),
    t("Ich lerne am besten abends mit Karteikarten."),
  ];
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-10 text-center">
      <Faultier zustand={zustand} groesse={104} koerper folgen className="mb-1 lg:hidden" />
      <p className="text-sm font-semibold">{t("Worüber möchtest du reden?")}</p>
      <p className="max-w-md text-xs leading-5 text-muted-foreground">
        {t("Der Assistent kennt deine Dokumente, deinen Stundenplan, Prüfungen, Hausaufgaben und Termine. Was er über dich lernt, siehst du rechts.")}
      </p>
      {ohneModell ? (
        <p className="max-w-md text-xs leading-5 text-muted-foreground">
          {t("Noch kein Modell verbunden – ohne Modell antwortet er nur aus deinen Daten.")}{" "}
          <Link href="/ki" className="underline underline-offset-2 hover:text-foreground">
            {t("Modell verbinden")}
          </Link>
        </p>
      ) : null}
      <div className="flex max-w-lg flex-wrap justify-center gap-1.5">
        {beispiele.map((beispiel) => (
          <button
            key={beispiel}
            type="button"
            onClick={() => onBeispiel(beispiel)}
            className="rounded-md border border-border px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:border-foreground hover:text-foreground"
          >
            {beispiel}
          </button>
        ))}
      </div>
    </div>
  );
}

interface AssistentNachrichtProps {
  message: ViewMessage;
  /** Nur die juengste Antwort zeigt den aktuellen Zustand; aeltere bleiben ruhig und unbewegt. */
  zustand: FaultierZustand | null;
  kannVorlesen: boolean;
  spielt: boolean;
  onVorlesen: () => void;
  onStopp: () => void;
  onEintragen: (index: number, proposal: AssistantProposal) => void;
  onVerwerfen: (index: number) => void;
  onBewerten: (wert: "good" | "bad") => void;
}

function AssistentNachricht({ message, zustand, kannVorlesen, spielt, onVorlesen, onStopp, onEintragen, onVerwerfen, onBewerten }: AssistentNachrichtProps) {
  const reply = message.reply;
  const herkunft = reply ? describeSource(reply.source, reply.model) : null;
  return (
    <div className="flex max-w-[92%] gap-2.5">
      <Faultier zustand={zustand ?? "ruhe"} still={zustand === null} groesse={28} className="mt-0.5" />
      <div className="min-w-0 flex-1 space-y-2">
        <p className="whitespace-pre-wrap text-[13px] leading-6">{message.content}</p>

        {reply && reply.proposals.length > 0 ? (
          <ul className="space-y-1.5">
            {reply.proposals.map((proposal, index) => {
              const state = message.proposalStates?.[index] ?? "offen";
              const note = message.proposalNotes?.[index];
              return (
                <li key={`${proposal.kind}-${index}`} className={cn("rounded-md border border-border px-3 py-2", state === "verworfen" && "opacity-50")}>
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="text-[13px] font-medium">
                        <span className="text-muted-foreground">{ACTION_LABELS[proposal.kind]}:</span> {proposal.title}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        {describeProposalTime(proposal)}
                        {proposal.subject_name ? ` · ${proposal.subject_name}${proposal.subject_id === null ? ` ${t("(Fach nicht gefunden)")}` : ""}` : ""}
                      </p>
                      {proposal.description ? <p className="mt-0.5 text-xs text-muted-foreground">{proposal.description}</p> : null}
                    </div>
                    {state === "eingetragen" ? (
                      <Badge variant="success">
                        <Check className="mr-1 h-3 w-3" aria-hidden="true" />
                        {t("Eingetragen")}
                      </Badge>
                    ) : state === "verworfen" ? (
                      <Badge variant="outline">{t("Verworfen")}</Badge>
                    ) : (
                      <div className="flex gap-1.5">
                        <Button type="button" size="sm" onClick={() => onEintragen(index, proposal)} disabled={state === "laeuft"}>
                          {state === "laeuft" ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <Plus aria-hidden="true" />}
                          {t("Eintragen")}
                        </Button>
                        <Button type="button" size="sm" variant="ghost" onClick={() => onVerwerfen(index)} aria-label={t("Vorschlag verwerfen")}>
                          <X aria-hidden="true" />
                        </Button>
                      </div>
                    )}
                  </div>
                  {note && state !== "eingetragen" ? (
                    <p className="mt-1 text-xs" role="alert">
                      {note}
                    </p>
                  ) : null}
                </li>
              );
            })}
          </ul>
        ) : null}

        {reply && reply.memories_added.length > 0 ? (
          <p className="text-xs text-muted-foreground">{t("Gemerkt:")}{" "}{reply.memories_added.map((memory) => memory.content).join(" · ")}</p>
        ) : null}

        <div className="flex flex-wrap items-center gap-1.5">
          {herkunft ? <Badge variant={herkunft.fromModel ? "info" : "outline"}>{herkunft.text}</Badge> : null}
          {reply?.grades_shared ? <Badge variant="outline">{t("Noten mitgeschickt")}</Badge> : null}
          {kannVorlesen ? (
            <button
              type="button"
              onClick={spielt ? onStopp : onVorlesen}
              className="inline-flex items-center gap-1 rounded-[5px] px-1.5 py-0.5 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
            >
              {spielt ? <VolumeX className="h-3.5 w-3.5" aria-hidden="true" /> : <Volume2 className="h-3.5 w-3.5" aria-hidden="true" />}
              {spielt ? t("Stopp") : t("Vorlesen")}
            </button>
          ) : null}
          {reply?.training_example_id != null ? (
            <span className="inline-flex items-center gap-0.5" role="group" aria-label={t("Antwort bewerten (fürs Training)")}>
              {(["good", "bad"] as const).map((wert) => {
                const aktiv = message.bewertung === wert;
                const Icon = wert === "good" ? ThumbsUp : ThumbsDown;
                return (
                  <button
                    key={wert}
                    type="button"
                    onClick={() => onBewerten(wert)}
                    aria-pressed={aktiv}
                    title={wert === "good" ? t("Gute Antwort") : t("Antwort lag daneben")}
                    className={cn(
                      "inline-flex items-center rounded-[5px] px-1.5 py-0.5 text-muted-foreground hover:bg-muted hover:text-foreground",
                      aktiv && "bg-muted text-foreground",
                    )}
                  >
                    <Icon className="h-3.5 w-3.5" fill={aktiv ? "currentColor" : "none"} aria-hidden="true" />
                    <span className="sr-only">{wert === "good" ? t("Gute Antwort") : t("Antwort lag daneben")}</span>
                  </button>
                );
              })}
            </span>
          ) : null}
        </div>

        {reply && reply.sources.length > 0 ? (
          <details className="text-xs text-muted-foreground">
            <summary className="cursor-pointer select-none hover:text-foreground">
              {reply.sources.length} {reply.sources.length === 1 ? t("Quelle") : t("Quellen")} {t("aus deinen Unterlagen")}
            </summary>
            <ul className="mt-1.5 space-y-1.5">
              {reply.sources.map((source) => (
                <li key={`${source.document_id}-${source.chunk_index}`} className="rounded-md border border-border px-3 py-2">
                  <p className="font-medium text-foreground">
                    {source.filename}
                    <span className="ml-2 font-normal text-muted-foreground">{t("Abschnitt {n}", { n: source.chunk_index + 1 })}</span>
                  </p>
                  <p className="mt-0.5 line-clamp-2 leading-5">{source.content}</p>
                </li>
              ))}
            </ul>
          </details>
        ) : null}
      </div>
    </div>
  );
}

/** Was der Assistent über dich weiß. Lädt neu, sobald eine Antwort etwas Neues gemerkt hat. */
function GedaechtnisCard({ messages }: { messages: ViewMessage[] }) {
  const [memories, setMemories] = useState<AssistantMemory[]>([]);
  const [neu, setNeu] = useState("");
  const [fehler, setFehler] = useState<string | null>(null);
  const gemerkt = messages.reduce((summe, message) => summe + (message.reply?.memories_added.length ?? 0), 0);

  useEffect(() => {
    let aktiv = true;
    getAssistantMemories()
      .then((liste) => {
        if (aktiv) {
          setMemories(liste);
        }
      })
      .catch(() => undefined);
    return () => {
      aktiv = false;
    };
  }, [gemerkt]);

  async function hinzufuegen(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = neu.trim();
    if (!text) {
      return;
    }
    setFehler(null);
    try {
      const memory = await addAssistantMemory(text);
      setMemories((aktuell) => [memory, ...aktuell]);
      setNeu("");
    } catch (ursache) {
      setFehler(fehlertext(ursache, t("Konnte nicht gespeichert werden.")));
    }
  }

  async function vergessen(id: number) {
    setFehler(null);
    try {
      await deleteAssistantMemory(id);
      setMemories((aktuell) => aktuell.filter((memory) => memory.id !== id));
    } catch (ursache) {
      setFehler(fehlertext(ursache, t("Konnte nicht gelöscht werden.")));
    }
  }

  return (
    <Card className="self-start">
      <CardHeader className="pb-3">
        <CardTitle>{t("Was er über dich weiß")}</CardTitle>
        <CardDescription>{t("Der Assistent merkt sich Vorlieben und Ziele aus euren Gesprächen. Alles hier steht bei jeder Frage im Kontext.")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <form onSubmit={(event) => void hinzufuegen(event)} className="flex gap-1.5">
          <label htmlFor="gedaechtnis-neu" className="sr-only">
            {t("Neue Notiz")}
          </label>
          <Input id="gedaechtnis-neu" value={neu} onChange={(event) => setNeu(event.target.value)} placeholder={t("z. B. Schwäche: Bruchrechnen")} className="h-8" maxLength={400} />
          <Button type="submit" size="sm" variant="outline" disabled={!neu.trim()} aria-label={t("Notiz hinzufügen")}>
            <Plus aria-hidden="true" />
          </Button>
        </form>
        {fehler ? (
          <p className="text-xs" role="alert">
            {fehler}
          </p>
        ) : null}
        {memories.length === 0 ? (
          <p className="text-xs leading-5 text-muted-foreground">{t("Noch nichts. Erzähl ihm z. B., wie du am liebsten lernst.")}</p>
        ) : (
          <ul className="divide-y divide-border">
            {memories.map((memory) => (
              <li key={memory.id} className="flex items-start justify-between gap-2 py-1.5">
                <p className="text-xs leading-5">
                  {memory.content}
                  {memory.source === "manual" ? <span className="ml-1 text-muted-foreground">{t("(von dir)")}</span> : null}
                </p>
                <button
                  type="button"
                  onClick={() => void vergessen(memory.id)}
                  className="shrink-0 rounded-[5px] p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                  aria-label={`„${memory.content}“ vergessen`}
                >
                  <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                </button>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
