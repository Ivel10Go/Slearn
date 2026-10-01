"use client";

import { useCallback, useState, type FormEvent } from "react";
import { CornerDownLeft, FileText, FolderTree, LoaderCircle, MessageSquareText, RefreshCw, Search, Upload } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { FormError } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { SectionBanner } from "@/components/manage/row-actions";
import { askDocuments, getDocuments, searchDocuments, uploadFile, type DocumentAskInput, type DocumentSearchInput } from "@/lib/api";
import { describeSource } from "@/lib/ai-runtime";
import { formatDateTime } from "@/lib/format";
import { useAction, useList } from "@/lib/use-collection";
import { cn } from "@/lib/utils";
import type { DocumentAnswer, DocumentRecord, DocumentSearchResult, FileUploadResult } from "@/lib/types";

/** `POST /v1/files` nimmt eine Datei entgegen. Das Backend kennt keine Größengrenze über HTTP. */
const MAX_UPLOAD_HINWEIS = 10 * 1024 * 1024;

const STATUS_LABEL: Record<string, string> = {
  indexed: "Indexiert",
  pending: "In Arbeit",
  processed: "Verarbeitet",
  error: "Fehler",
  failed: "Fehlgeschlagen",
};

function statusLabel(status: string) {
  return STATUS_LABEL[status] ?? status;
}

/**
 * Die Dokumentliste liest das Backend.
 *
 * Zu `GET /v1/documents` gibt es kein Schreiben ausser dem Upload, und kein
 * Loeschen. `useList` statt `useCollection` - dort muesste man vier Funktionen
 * hinstellen, die einen Fehler werfen, und damit zum Aufruf einladen.
 */
function useDocuments() {
  return useList<DocumentRecord>(() => getDocuments());
}

/**
 * Die Herkunft einer Antwort, in derselben Formulierung wie im Dashboard.
 *
 * `describeSource` kommt aus `lib/ai-runtime` - dieselbe Funktion wie in der
 * Kopfzeile. Zwei Texte fuer dieselbe Frage waeren zwei Herkunftssysteme, und
 * genau die soll es nicht geben.
 */
function AntwortHerkunft({ antwort }: { antwort: DocumentAnswer }) {
  const herkunft = describeSource(antwort.source, antwort.model);
  if (herkunft.fromModel) {
    return <Badge variant="secondary">{herkunft.text}</Badge>;
  }
  if (antwort.source === "none") {
    return <Badge variant="outline">{herkunft.text}</Badge>;
  }
  return <Badge variant="info">Aus deinen Dokumenten</Badge>;
}

export function DocumentsView() {
  const sammlung = useDocuments();
  const frage = useAction<DocumentAnswer>();
  const suche = useAction<DocumentSearchResult>();
  const upload = useAction<FileUploadResult>();

  const [frageText, setFrageText] = useState("");
  const [suchText, setSuchText] = useState("");
  const [datei, setDatei] = useState<File | null>(null);
  const [uploadHinweis, setUploadHinweis] = useState<string | null>(null);

  const hochladen = useCallback(async () => {    if (!datei) {
      setUploadHinweis("Wähle zuerst eine Datei aus.");
      return;
    }
    if (datei.size > MAX_UPLOAD_HINWEIS) {
      // Das Backend begrenzt die Größe nicht. Ein 400-MB-Upload würde erst
      // nach dem Hochladen scheitern - nach der Wartezeit, die niemandem
      // nützt. Die Zahl ist eine Obergrenze für die Oberfläche, keine
      // Zusage des Servers.
      setUploadHinweis("Bitte wähle eine Datei bis 10 MB.");
      return;
    }
    setUploadHinweis(null);
    const ergebnis = await upload.run(() => uploadFile(datei));
    if (ergebnis) {
      setDatei(null);
      await sammlung.reload();
    }
  }, [datei, sammlung, upload]);

  async function fragen(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = frageText.trim();
    if (!text) {
      return;
    }
    const eingabe: DocumentAskInput = { question: text };
    await frage.run(() => askDocuments(eingabe));
  }

  async function suchen(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = suchText.trim();
    if (!text) {
      return;
    }
    const eingabe: DocumentSearchInput = { query: text };
    await suche.run(() => searchDocuments(eingabe));
  }

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-[22px] font-semibold leading-8 tracking-tight">Dokumente</h1>
        <p className="mt-1 max-w-2xl text-[13px] leading-5 text-muted-foreground">
          Lade Unterlagen hoch, durchsuche sie und stell dem Assistenten Fragen. Ohne verbundenen KI-Dienst wird aus den Treffern
          zitiert.
        </p>
      </header>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(320px,0.8fr)]">
        <div className="space-y-4">
          <Card className="border-border/80 bg-card/90">
            <CardHeader className="flex-row flex-wrap items-start justify-between gap-3 space-y-0 pb-3">
              <div>
                <CardTitle>Bibliothek</CardTitle>
                <CardDescription className="mt-1">
                  {sammlung.items.length} {sammlung.items.length === 1 ? "Dokument" : "Dokumente"} im Bestand.
                </CardDescription>
              </div>
              <Button type="button" size="icon" variant="ghost" onClick={() => void sammlung.reload()} aria-label="Dokumente neu laden" disabled={sammlung.status === "loading"}>
                <RefreshCw className={cn(sammlung.status === "loading" && "animate-spin")} aria-hidden="true" />
              </Button>
            </CardHeader>

            <CardContent className="space-y-4">
              {/*
                Der Ladefehler und der Upload-Fehler teilen sich eine Box,
                weil beide an derselben Stelle stehen und beide bedeuten:
                "hier ist gerade nichts passiert". Die Meldung des Uploads
                steht zusaetzlich beim Formular, dort wo sie entstanden ist.
              */}
              <SectionBanner
                error={upload.error ? null : sammlung.error}
                actionError={upload.error}
                onRetry={() => void sammlung.reload()}
              />

              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  void hochladen();
                }}
                className="space-y-3 rounded-md border border-dashed border-border bg-muted/30 p-4"
              >
                <DateiAuswahl datei={datei} setDatei={setDatei} disabled={upload.pending} />
                <div className="flex flex-wrap items-center gap-3">
                  <Button type="submit" size="sm" disabled={upload.pending || !datei}>
                    {upload.pending ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <Upload aria-hidden="true" />}
                    {upload.pending ? "Wird hochgeladen …" : "Hochladen"}
                  </Button>
                </div>
                <FormError message={uploadHinweis ?? upload.error} />
                <p className="text-xs leading-5 text-muted-foreground">
                  Indexiert werden Textdateien (<span className="font-mono">.txt</span>, <span className="font-mono">.md</span>). Andere
                  Formate werden gespeichert, aber nicht durchsuchbar gemacht. Ein ganzer Ordner lässt sich unter{" "}
                  <span className="font-semibold">Einstellungen</span> über eine Dokumentverbindung indexieren.
                </p>
              </form>

              {sammlung.status === "loading" && sammlung.items.length === 0 ? (
                <div className="space-y-3" aria-busy="true" aria-label="Dokumente werden geladen">
                  <Skeleton className="h-16 w-full" />
                  <Skeleton className="h-16 w-full" />
                </div>
              ) : sammlung.items.length === 0 ? (
                <EmptyState icon={FolderTree} title="Noch keine Dokumente" description="Lade eine Datei hoch oder verbinde unter den Einstellungen einen Dokumentordner." />
              ) : (
                <ul className="space-y-2.5">
                  {sammlung.items.map((dokument) => (
                    <li key={dokument.id} className="flex items-start gap-3 border border-border bg-card/60 p-3.5">
                      <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center bg-muted">
                        <FileText className="h-4 w-4" aria-hidden="true" />
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <p className="truncate text-sm font-semibold">{dokument.filename}</p>
                          <Badge variant={dokument.status === "error" || dokument.status === "failed" ? "warning" : "secondary"}>
                            {statusLabel(dokument.status)}
                          </Badge>
                        </div>
                        <p className="mt-1 text-xs font-semibold text-muted-foreground">
                          {dokument.source === "upload" ? "hochgeladen" : `aus ${dokument.source}`}
                          <span className="text-border"> · </span>
                          {dokument.processed_at ? `verarbeitet ${formatDateTime(dokument.processed_at)}` : `angelegt ${formatDateTime(dokument.created_at)}`}
                        </p>
                        {dokument.mime_type ? <p className="mt-0.5 text-xs text-muted-foreground">{dokument.mime_type}</p> : null}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>

          <Card className="border-border/80 bg-card/90">
            <CardHeader>
              <CardTitle>Durchsuchen</CardTitle>
              <CardDescription className="mt-1">Findet Textstellen, ohne ein Modell zu befragen.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <form onSubmit={(event) => void suchen(event)} className="flex gap-2" role="search">
                <label htmlFor="dokument-suche" className="sr-only">
                  Dokumente durchsuchen
                </label>
                <Input
                  id="dokument-suche"
                  type="search"
                  value={suchText}
                  onChange={(event) => setSuchText(event.target.value)}
                  placeholder="Zum Beispiel:Photosynthese"
                  disabled={suche.pending}
                  className="min-w-0 flex-1"
                />
                <Button type="submit" disabled={suche.pending || !suchText.trim()}>
                  {suche.pending ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <Search aria-hidden="true" />}
                  Suchen
                </Button>
              </form>

              <FormError message={suche.error} />

              {suche.result ? (
                <div className="space-y-2.5">
                  <p className="text-xs font-medium text-muted-foreground">
                    {suche.result.hits.length} Treffer zu „{suche.result.query}“
                  </p>
                  {suche.result.hits.length === 0 ? (
                    <EmptyState icon={Search} title="Nichts gefunden" description="Für diese Formulierung gibt es keinen Treffer in deinen Dokumenten." />
                  ) : (
                    <ul className="space-y-2.5">
                      {suche.result.hits.map((treffer) => (
                        <li key={`${treffer.document_id}-${treffer.chunk_index}`} className="border border-border bg-card/60 p-3">
                          <p className="text-xs font-semibold">
                            {treffer.filename}
                            <span className="ml-2 font-semibold text-muted-foreground">Abschnitt {treffer.chunk_index + 1}</span>
                            {treffer.distance !== null ? (
                              <span className="ml-2 font-mono font-semibold text-muted-foreground">{treffer.distance.toFixed(3)}</span>
                            ) : null}
                          </p>
                          <p className="mt-1 line-clamp-3 text-xs leading-5 text-muted-foreground">{treffer.content}</p>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              ) : null}
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Frag deine Unterlagen</CardTitle>
            <CardDescription className="mt-1">Eine Frage zu deinen Unterlagen, mit Quellenangabe.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <form onSubmit={(event) => void fragen(event)} className="flex gap-2" role="search">
              <label htmlFor="dokument-frage" className="sr-only">
                Frage an deine Dokumente
              </label>
              <Input
                id="dokument-frage"
                type="text"
                value={frageText}
                onChange={(event) => setFrageText(event.target.value)}
                placeholder="Was steht im Skript zum Zellzyklus?"
                disabled={frage.pending}
                className="min-w-0 flex-1"
              />
              <Button type="submit" disabled={frage.pending || !frageText.trim()}>
                {frage.pending ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <CornerDownLeft aria-hidden="true" />}
                Fragen
              </Button>
            </form>

            <FormError message={frage.error} />

            {frage.result ? (
              <div className="space-y-3">
                <div className="flex flex-wrap items-center gap-2">
                  <AntwortHerkunft antwort={frage.result} />
                  {frage.result.grades_shared ? <Badge variant="info">Noten mitgeschickt</Badge> : <Badge variant="outline">Noten nicht mitgeschickt</Badge>}
                </div>
                <p className="border border-border bg-background/60 px-4 py-3 text-sm leading-6">{frage.result.answer}</p>
                {frage.result.sources.length > 0 ? (
                  <ul className="space-y-2">
                    {frage.result.sources.map((quelle) => (
                      <li key={`${quelle.document_id}-${quelle.chunk_index}`} className="border border-border/70 bg-muted/30 px-3 py-2.5">
                        <p className="text-xs font-semibold">
                          {quelle.filename}
                          <span className="ml-2 font-semibold text-muted-foreground">Abschnitt {quelle.chunk_index + 1}</span>
                        </p>
                        <p className="mt-1 line-clamp-2 text-xs leading-5 text-muted-foreground">{quelle.content}</p>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-xs text-muted-foreground">Es wurden keine passenden Stellen gefunden.</p>
                )}
              </div>
            ) : (
              <div className="flex min-h-28 flex-col items-center justify-center gap-2 rounded-md border border-dashed border-border px-5 py-8 text-center" role="status">
                <MessageSquareText className="h-5 w-5 text-muted-foreground" strokeWidth={1.75} aria-hidden="true" />
                <p className="text-sm font-semibold">Noch keine Frage</p>
                <p className="max-w-md text-xs leading-5 text-muted-foreground">Der Assistent antwortet aus deinen eigenen Dokumenten.</p>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function DateiAuswahl({ datei, setDatei, disabled }: { datei: File | null; setDatei: (file: File | null) => void; disabled: boolean }) {
  return (
    <div className="space-y-2">
      <label htmlFor="dokument-datei" className="block text-sm font-semibold">
        Datei
      </label>
      <input
        id="dokument-datei"
        type="file"
        disabled={disabled}
        onChange={(event) => setDatei(event.target.files?.[0] ?? null)}
        className="block w-full text-sm text-foreground file:mr-3 file:border file:border-border file:bg-muted file:px-3 file:py-1.5 file:text-sm file:font-semibold hover:file:bg-accent disabled:opacity-50"
      />
      {datei ? (
        <p className="text-xs font-semibold text-muted-foreground">
          Gewählt: {datei.name} · {(datei.size / 1024).toFixed(0)} KB
        </p>
      ) : null}
    </div>
  );
}
