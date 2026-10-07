"use client";

import { useEffect, useState, type FormEvent } from "react";
import { LoaderCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError, getSubjects } from "@/lib/api";
import { answerQuiz, getLearnerProfile, startQuiz, type QuizQuestion, type QuizResult } from "@/lib/assistant";
import type { Subject } from "@/lib/types";

/*
 * Abfrage aus dem eigenen Material und das Lernprofil - beides in der
 * Seitenleiste des Assistenten (Startseite).
 *
 * Die Abfrage ist bewusst kein Chat: eine Frage, ein Antwortfeld, ein Urteil
 * mit der Stelle aus dem Material. Das Ergebnis aendert den Lernstand des
 * Themas, deshalb soll es nicht in einem Gespraechsverlauf verschwinden.
 */

const URTEIL: Record<QuizResult["verdict"], string> = {
  richtig: "Richtig",
  teilweise: "Teilweise",
  falsch: "Noch nicht",
};

function fehlertext(ursache: unknown, ersatz: string) {
  return ursache instanceof ApiError && ursache.message ? ursache.message : ersatz;
}

export function AbfrageCard({ onErgebnis }: { onErgebnis?: () => void }) {
  const [faecher, setFaecher] = useState<Subject[]>([]);
  const [fach, setFach] = useState<string>("");
  const [frage, setFrage] = useState<QuizQuestion | null>(null);
  const [antwort, setAntwort] = useState("");
  const [ergebnis, setErgebnis] = useState<QuizResult | null>(null);
  const [fehler, setFehler] = useState<string | null>(null);
  const [beschaeftigt, setBeschaeftigt] = useState(false);

  useEffect(() => {
    let aktiv = true;
    getSubjects()
      .then((liste) => {
        if (aktiv) {
          setFaecher(liste);
        }
      })
      .catch(() => undefined);
    return () => {
      aktiv = false;
    };
  }, []);

  async function naechste() {
    setBeschaeftigt(true);
    setFehler(null);
    try {
      setFrage(await startQuiz(fach ? Number(fach) : null));
      setAntwort("");
      setErgebnis(null);
    } catch (ursache) {
      setFehler(fehlertext(ursache, "Es ließ sich keine Frage stellen."));
    } finally {
      setBeschaeftigt(false);
    }
  }

  async function pruefen(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!frage || !antwort.trim()) {
      return;
    }
    setBeschaeftigt(true);
    setFehler(null);
    try {
      setErgebnis(await answerQuiz(frage.question_id, antwort.trim()));
      onErgebnis?.();
    } catch (ursache) {
      setFehler(fehlertext(ursache, "Die Antwort ließ sich nicht prüfen."));
    } finally {
      setBeschaeftigt(false);
    }
  }

  return (
    <Card className="self-start">
      <CardHeader className="pb-3">
        <CardTitle>Abfragen</CardTitle>
        <CardDescription>Fragen aus deinen Aufschrieben und Arbeitsblättern. Das Ergebnis zählt für deinen Lernstand.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-[13px]">
        <div className="flex gap-2">
          <select
            aria-label="Fach für die Abfrage"
            value={fach}
            onChange={(event) => setFach(event.target.value)}
            className="h-8 min-w-0 flex-1 rounded-md border border-border bg-background px-2 text-[13px]"
          >
            <option value="">Alle Fächer</option>
            {faecher.map((eintrag) => (
              <option key={eintrag.id} value={eintrag.id}>
                {eintrag.name}
              </option>
            ))}
          </select>
          <Button type="button" size="sm" onClick={() => void naechste()} disabled={beschaeftigt}>
            {beschaeftigt && !frage ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : null}
            {frage ? "Nächste Frage" : "Frag mich ab"}
          </Button>
        </div>

        {fehler ? (
          <p className="rounded-md border border-foreground/30 bg-muted px-3 py-2" role="alert">
            {fehler}
          </p>
        ) : null}

        {frage ? (
          <form onSubmit={(event) => void pruefen(event)} className="space-y-2 border-t border-border pt-3">
            <p className="font-medium">{frage.question}</p>
            <p className="text-xs text-muted-foreground">aus {frage.filename}</p>
            <textarea
              aria-label="Deine Antwort"
              value={antwort}
              onChange={(event) => setAntwort(event.target.value)}
              rows={3}
              disabled={beschaeftigt || ergebnis !== null}
              className="w-full resize-y rounded-md border border-border bg-background px-2 py-1.5 text-[13px]"
            />
            {ergebnis === null ? (
              <Button type="submit" size="sm" variant="outline" disabled={beschaeftigt || !antwort.trim()}>
                {beschaeftigt ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : null}
                Prüfen
              </Button>
            ) : null}
          </form>
        ) : null}

        {ergebnis ? (
          <div className="space-y-2 rounded-md border border-border px-3 py-2.5">
            <div className="flex items-center gap-2">
              <Badge variant={ergebnis.verdict === "richtig" ? "default" : "outline"}>{URTEIL[ergebnis.verdict]}</Badge>
              {ergebnis.topic && ergebnis.confidence_label ? (
                <span className="text-xs text-muted-foreground">
                  {ergebnis.topic}: jetzt „{ergebnis.confidence_label}“
                </span>
              ) : null}
            </div>
            <p>{ergebnis.explanation}</p>
            {ergebnis.verdict !== "richtig" ? <p className="text-xs">Musterlösung: {ergebnis.reference}</p> : null}
            {ergebnis.excerpt ? (
              <details className="text-xs text-muted-foreground">
                <summary className="cursor-pointer">Stelle in {ergebnis.filename}</summary>
                <p className="mt-1 whitespace-pre-line">{ergebnis.excerpt}</p>
              </details>
            ) : null}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

export function LernprofilCard({ refreshKey }: { refreshKey: number }) {
  const [zeilen, setZeilen] = useState<string[] | null>(null);

  useEffect(() => {
    let aktiv = true;
    getLearnerProfile()
      .then((liste) => {
        if (aktiv) {
          setZeilen(liste);
        }
      })
      .catch(() => undefined);
    return () => {
      aktiv = false;
    };
  }, [refreshKey]);

  return (
    <Card className="self-start">
      <CardHeader className="pb-3">
        <CardTitle>Wie du lernst</CardTitle>
        <CardDescription>Steht bei jeder Frage im Kontext. Es kommt aus deinem Lernstand, deinen Lernzeiten und Abfragen.</CardDescription>
      </CardHeader>
      <CardContent className="text-[13px]">
        {zeilen === null ? null : zeilen.length === 0 ? (
          <p className="text-xs text-muted-foreground">Noch nichts – das füllt sich, wenn du lernst, Themen einschätzt oder dich abfragen lässt.</p>
        ) : (
          <ul className="space-y-1.5">
            {zeilen.map((zeile) => (
              <li key={zeile}>{zeile}</li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
