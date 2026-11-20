"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { LoaderCircle, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { ApiError, createExam, getExams, getSubjects, uploadFile } from "@/lib/api";
import { formatDate, formatDateTime } from "@/lib/format";
import {
  CREATION_KINDS,
  createCreation,
  createNotebookPlan,
  deleteCreation,
  getNotebook,
  mindmapLines,
  setNotebookDocuments,
  type Creation,
  type CreationKind,
  type Notebook,
  type NotebookDocument,
} from "@/lib/notebook";
import type { Exam, Subject } from "@/lib/types";
import { cn } from "@/lib/utils";
import { t, tn, translateServerText } from "@/lib/i18n";

/*
 * Klassenarbeit als Arbeitsbereich (wie NotebookLM): links die Klassenarbeiten,
 * rechts Material, Lernplan und Studio der gewaehlten. Die Wahl steht in `?id=`,
 * nicht im Hash - die Shell liest den Hash fuer den Aktivzustand.
 */

const KIND_LABEL: Record<CreationKind, string> = {
  summary: t("Zusammenfassung"),
  cheatsheet: t("Lernzettel"),
  mindmap: t("Mindmap"),
  flashcards: t("Karteikarten"),
  glossary: t("Glossar"),
  practice_exam: t("Probeklausur"),
};

function fehlertext(ursache: unknown) {
  return ursache instanceof ApiError && ursache.message ? translateServerText(ursache.message) : t("Das hat nicht geklappt.");
}

function gewaehlteId(): number | null {
  const wert = Number(new URLSearchParams(window.location.search).get("id"));
  return Number.isInteger(wert) && wert > 0 ? wert : null;
}

export function NotebookView() {
  const [exams, setExams] = useState<Exam[]>([]);
  const [subjects, setSubjects] = useState<Subject[]>([]);
  const [examId, setExamId] = useState<number | null>(null);
  const [fehler, setFehler] = useState<string | null>(null);

  const ladeExams = useCallback(async () => {
    try {
      const liste = await getExams(true);
      setExams(liste);
      setExamId((aktuell) => aktuell ?? gewaehlteId() ?? liste[0]?.id ?? null);
    } catch (ursache) {
      setFehler(fehlertext(ursache));
    }
  }, []);

  useEffect(() => {
    window.setTimeout(() => void ladeExams(), 0);
    getSubjects().then(setSubjects).catch(() => undefined);
  }, [ladeExams]);

  function waehle(id: number) {
    setExamId(id);
    const url = new URL(window.location.href);
    url.searchParams.set("id", String(id));
    window.history.replaceState(null, "", url);
  }

  const exam = exams.find((item) => item.id === examId) ?? null;

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-[26px] font-semibold leading-9 tracking-tight">{t("Klassenarbeiten")}</h1>
        <p className="sr-only">
          {t("Material hochladen, Termin eintragen: daraus entstehen ein Lernplan und Lernhilfen wie Zusammenfassung, Karteikarten oder eine Probeklausur.")}
        </p>
      </header>
      {fehler ? <p className="text-[15px] text-destructive">{fehler}</p> : null}

      <div className="grid items-start gap-4 lg:grid-cols-[260px_1fr]">
        <Card>
          <CardHeader>
            <CardTitle>{t("Anstehend")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <ul className="space-y-1">
              {exams.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    onClick={() => waehle(item.id)}
                    className={cn(
                      "w-full rounded-md px-2 py-1.5 text-left text-[15px] hover:bg-muted",
                      item.id === examId && "bg-muted font-medium",
                    )}
                  >
                    <span className="block">{item.title}</span>
                    <span className="block text-[13px] text-muted-foreground">
                      {[item.subject_name, formatDate(item.date, { day: "2-digit", month: "2-digit", year: "numeric" })]
                        .filter(Boolean)
                        .join(" · ")}
                    </span>
                  </button>
                </li>
              ))}
              {exams.length === 0 ? <li className="text-[15px] text-muted-foreground">{t("Noch keine Klassenarbeit eingetragen.")}</li> : null}
            </ul>
            <NeueKlassenarbeit
              subjects={subjects}
              onCreated={async (neu) => {
                await ladeExams();
                waehle(neu.id);
              }}
            />
          </CardContent>
        </Card>

        {exam ? <Arbeitsbereich key={exam.id} exam={exam} /> : null}
      </div>
    </div>
  );
}

function NeueKlassenarbeit({ subjects, onCreated }: { subjects: Subject[]; onCreated: (exam: Exam) => Promise<void> }) {
  const [titel, setTitel] = useState("");
  const [fach, setFach] = useState("");
  const [datum, setDatum] = useState("");
  const [fehler, setFehler] = useState<string | null>(null);
  const [beschaeftigt, setBeschaeftigt] = useState(false);

  async function anlegen(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBeschaeftigt(true);
    setFehler(null);
    try {
      const neu = await createExam({
        title: titel.trim(),
        date: new Date(`${datum}T08:00:00`).toISOString(),
        subject_id: fach ? Number(fach) : null,
      });
      setTitel("");
      setDatum("");
      await onCreated(neu);
    } catch (ursache) {
      setFehler(fehlertext(ursache));
    } finally {
      setBeschaeftigt(false);
    }
  }

  return (
    <form onSubmit={(event) => void anlegen(event)} className="space-y-2 border-t border-border pt-3">
      <p className="text-[15px] font-medium">{t("Neue Klassenarbeit")}</p>
      <Input aria-label={t("Titel")} placeholder={t("Titel")} value={titel} onChange={(e) => setTitel(e.target.value)} required />
      <Select aria-label={t("Fach")} value={fach} onChange={(e) => setFach(e.target.value)} required>
        <option value="">{t("Fach wählen")}</option>
        {subjects.map((subject) => (
          <option key={subject.id} value={subject.id}>
            {subject.name}
          </option>
        ))}
      </Select>
      <Input aria-label={t("Datum")} type="date" value={datum} onChange={(e) => setDatum(e.target.value)} required />
      {fehler ? <p className="text-[13px] text-destructive">{fehler}</p> : null}
      <Button type="submit" size="sm" disabled={beschaeftigt || !titel.trim() || !datum || !fach}>
        {t("Anlegen")}
      </Button>
    </form>
  );
}

function Arbeitsbereich({ exam }: { exam: Exam }) {
  const [notebook, setNotebook] = useState<Notebook | null>(null);
  const [fehler, setFehler] = useState<string | null>(null);
  const [laeuft, setLaeuft] = useState<string | null>(null);
  const [planText, setPlanText] = useState<string | null>(null);
  const [offen, setOffen] = useState<number | null>(null);

  const laden = useCallback(async () => {
    try {
      setNotebook(await getNotebook(exam.id));
    } catch (ursache) {
      setFehler(fehlertext(ursache));
    }
  }, [exam.id]);

  useEffect(() => {
    window.setTimeout(() => void laden(), 0);
  }, [laden]);

  async function schritt(name: string, aktion: () => Promise<void>) {
    setLaeuft(name);
    setFehler(null);
    try {
      await aktion();
    } catch (ursache) {
      setFehler(fehlertext(ursache));
    } finally {
      setLaeuft(null);
    }
  }

  /** Speichert die Auswahl; `aenderung` setzt ein Dokument auf aus, ganzer Text oder Kurzfassung. */
  function speichern(aenderung: { id: number; stufe: "aus" | "voll" | "kurz" }) {
    if (!notebook) return;
    const stufe = (doc: NotebookDocument) =>
      doc.id === aenderung.id ? aenderung.stufe : !doc.selected ? "aus" : doc.summary ? "kurz" : "voll";
    const ids = notebook.documents.filter((doc) => stufe(doc) !== "aus").map((doc) => doc.id);
    const kurz = notebook.documents.filter((doc) => stufe(doc) === "kurz").map((doc) => doc.id);
    void schritt("material", async () => {
      await setNotebookDocuments(exam.id, ids, kurz);
      await laden();
    });
  }

  function hochladen(datei: File | undefined) {
    if (!datei || !notebook) return;
    const bisher = notebook.documents.filter((doc) => doc.selected).map((doc) => doc.id);
    const kurz = notebook.documents.filter((doc) => doc.selected && doc.summary).map((doc) => doc.id);
    void schritt("upload", async () => {
      const neu = await uploadFile(datei);
      await setNotebookDocuments(exam.id, [...bisher, neu.id], kurz);
      await laden();
    });
  }

  const gewaehlt = notebook?.documents.filter((doc) => doc.selected).length ?? 0;
  const tage = exam.days_until;

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-[20px] font-semibold">{exam.title}</h2>
        <p className="text-[15px] text-muted-foreground">
          {[exam.subject_name, formatDate(exam.date), tage !== null && tage >= 0 ? tn(tage, "noch {n} Tag", "noch {n} Tage") : null]
            .filter(Boolean)
            .join(" · ")}
        </p>
      </div>
      {fehler ? <p className="text-[15px] text-destructive">{fehler}</p> : null}

      <div className="grid items-start gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>{t("Material")}</CardTitle>
            <CardDescription>{tn(gewaehlt, "{n} Dokument ausgewählt", "{n} Dokumente ausgewählt")}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <label className="block text-[15px]">
              <span className="mb-1 block text-muted-foreground">{t("Neues Material hochladen")}</span>
              <Input
                type="file"
                disabled={laeuft !== null}
                onChange={(event) => {
                  hochladen(event.target.files?.[0]);
                  event.target.value = "";
                }}
              />
            </label>
            <ul className="max-h-80 space-y-1 overflow-y-auto">
              {notebook?.documents.map((doc) => (
                <li key={doc.id} className="flex items-start gap-2">
                  <label className="flex flex-1 items-start gap-2 text-[15px]">
                    <input
                      type="checkbox"
                      className="mt-1"
                      checked={doc.selected}
                      disabled={laeuft !== null}
                      onChange={() => speichern({ id: doc.id, stufe: doc.selected ? "aus" : "voll" })}
                    />
                    <span>
                      {doc.filename}
                      {doc.material_topic ? <span className="text-muted-foreground"> · {doc.material_topic}</span> : null}
                    </span>
                  </label>
                  {doc.selected ? (
                    <Select
                      className="h-8 w-auto shrink-0 px-2 text-[13px]"
                      value={doc.summary ? "kurz" : "voll"}
                      disabled={laeuft !== null}
                      aria-label={t("Wie viel von {datei} ins Modell geht", { datei: doc.filename })}
                      title={t("Kurzfassung spart Platz: mehr Material passt in ein kleines Modell.")}
                      onChange={(event) => speichern({ id: doc.id, stufe: event.target.value === "kurz" ? "kurz" : "voll" })}
                    >
                      <option value="voll">{t("Ganzer Text")}</option>
                      <option value="kurz">{t("Kurzfassung")}</option>
                    </Select>
                  ) : null}
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>{t("Lernplan")}</CardTitle>
            <CardDescription>
              {t("Die Themen kommen aus dem Material, verteilt auf die Tage vor der Klassenarbeit – das schwächste Thema zuerst.")}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            <Button
              disabled={laeuft !== null}
              onClick={() =>
                void schritt("plan", async () => {
                  const ergebnis = await createNotebookPlan(exam.id);
                  setPlanText(
                    ergebnis.created_count > 0
                      ? tn(ergebnis.created_count, "{n} Lernaufgabe angelegt.", "{n} Lernaufgaben angelegt.")
                      : t("Der Lernplan ist schon vollständig."),
                  );
                })
              }
            >
              {laeuft === "plan" ? <LoaderCircle className="animate-spin" /> : null}
              {t("Lernplan erstellen")}
            </Button>
            {planText ? (
              <p className="text-[15px]">
                {planText}{" "}
                <Link href="/lernen" className="underline underline-offset-4">
                  {t("Zum Lernplan")}
                </Link>
              </p>
            ) : null}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>{t("Studio")}</CardTitle>
          <CardDescription>{t("Erstellt aus dem ausgewählten Material. Das kann mit einem lokalen Modell eine Minute dauern.")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap gap-2">
            {CREATION_KINDS.map((kind) => (
              <Button
                key={kind}
                variant="outline"
                size="sm"
                disabled={laeuft !== null || gewaehlt === 0}
                onClick={() =>
                  void schritt(kind, async () => {
                    const neu = await createCreation(exam.id, kind);
                    setNotebook((alt) => (alt ? { ...alt, creations: [neu, ...alt.creations] } : alt));
                    setOffen(neu.id);
                  })
                }
              >
                {laeuft === kind ? <LoaderCircle className="animate-spin" /> : null}
                {KIND_LABEL[kind]}
              </Button>
            ))}
          </div>
          <ul className="divide-y divide-border border-y border-border">
            {notebook?.creations.map((creation) => (
              <li key={creation.id} className="py-2">
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    className="flex-1 text-left text-[15px] font-medium"
                    onClick={() => setOffen(offen === creation.id ? null : creation.id)}
                    aria-expanded={offen === creation.id}
                  >
                    {KIND_LABEL[creation.kind as CreationKind] ?? creation.kind}
                    <span className="ml-2 font-normal text-muted-foreground">{formatDateTime(creation.created_at)}</span>
                  </button>
                  <Badge variant="outline">{creation.source === "local" ? t("Lokal") : t("Online")}</Badge>
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={t("Löschen")}
                    onClick={() =>
                      void schritt("delete", async () => {
                        await deleteCreation(exam.id, creation.id);
                        setNotebook((alt) => (alt ? { ...alt, creations: alt.creations.filter((c) => c.id !== creation.id) } : alt));
                      })
                    }
                  >
                    <Trash2 />
                  </Button>
                </div>
                {offen === creation.id ? <Ansicht creation={creation} /> : null}
              </li>
            ))}
            {notebook && notebook.creations.length === 0 ? (
              <li className="py-2 text-[15px] text-muted-foreground">{t("Noch nichts erstellt.")}</li>
            ) : null}
          </ul>
        </CardContent>
      </Card>
    </div>
  );
}

function Ansicht({ creation }: { creation: Creation }) {
  const items = creation.items ?? [];
  if (creation.kind === "flashcards") {
    return <Karteikarten karten={items.map((item) => ({ vorne: item.front, hinten: item.back }))} />;
  }
  if (creation.kind === "glossary") {
    return (
      <dl className="mt-2 space-y-2 text-[15px]">
        {items.map((item, index) => (
          <div key={index}>
            <dt className="font-medium">{item.term}</dt>
            <dd className="whitespace-pre-wrap text-muted-foreground">{item.definition}</dd>
          </div>
        ))}
      </dl>
    );
  }
  if (creation.kind === "practice_exam") {
    return (
      <ol className="mt-2 list-decimal space-y-3 pl-5 text-[15px]">
        {items.map((item, index) => (
          <li key={index}>
            <p className="whitespace-pre-wrap">{item.question}</p>
            <details className="mt-1 text-muted-foreground">
              <summary className="cursor-pointer">{t("Lösung zeigen")}</summary>
              <p className="mt-1 whitespace-pre-wrap">{item.answer}</p>
            </details>
          </li>
        ))}
      </ol>
    );
  }
  if (creation.kind === "mindmap") {
    return (
      <ul className="mt-2 space-y-1 text-[15px]">
        {mindmapLines(creation.body).map((zeile, index) => (
          <li
            key={index}
            style={{ paddingLeft: `${zeile.depth * 18}px` }}
            className={cn(zeile.depth === 0 ? "font-semibold" : "border-l border-border pl-2", zeile.depth === 1 && "font-medium")}
          >
            {zeile.text}
          </li>
        ))}
      </ul>
    );
  }
  return <p className="mt-2 whitespace-pre-wrap text-[15px] leading-6">{creation.body}</p>;
}

function Karteikarten({ karten }: { karten: { vorne: string; hinten: string }[] }) {
  const [index, setIndex] = useState(0);
  const [umgedreht, setUmgedreht] = useState(false);
  const karte = karten[index];
  if (!karte) return null;

  function gehe(schritt: number) {
    setIndex((alt) => (alt + schritt + karten.length) % karten.length);
    setUmgedreht(false);
  }

  return (
    <div className="mt-2 space-y-2">
      <button
        type="button"
        onClick={() => setUmgedreht((alt) => !alt)}
        className="flex min-h-32 w-full items-center justify-center rounded-md border border-border p-4 text-center text-base hover:bg-muted"
      >
        <span className="whitespace-pre-wrap">{umgedreht ? karte.hinten : karte.vorne}</span>
      </button>
      <div className="flex items-center justify-between text-[13px] text-muted-foreground">
        <Button variant="ghost" size="sm" onClick={() => gehe(-1)}>
          {t("Zurück")}
        </Button>
        <span>
          {t("Karte {n} von {gesamt}", { n: index + 1, gesamt: karten.length })} · {t("Zum Umdrehen klicken")}
        </span>
        <Button variant="ghost" size="sm" onClick={() => gehe(1)}>
          {t("Weiter")}
        </Button>
      </div>
    </div>
  );
}
