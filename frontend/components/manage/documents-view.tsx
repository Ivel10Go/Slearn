"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { FileText, FolderTree, LoaderCircle, MessageSquareText, RefreshCw, Search, Upload } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { FormError } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { SectionBanner } from "@/components/manage/row-actions";
import { getDocuments, searchDocuments, updateDocumentMaterial, uploadFile, type DocumentSearchInput } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { useAction, useList } from "@/lib/use-collection";
import { cn } from "@/lib/utils";
import type { DocumentRecord, DocumentSearchResult, FileUploadResult, MaterialKind } from "@/lib/types";

/** `POST /v1/files` nimmt eine Datei entgegen. Das Backend kennt keine Größengrenze über HTTP. */
const MAX_UPLOAD_HINWEIS = 10 * 1024 * 1024;

const STATUS_LABEL: Record<string, string> = {
  indexed: "Indexiert",
  queued: "Wird abgeschrieben",
  needs_model: "Bildmodell fehlt",
  pending: "In Arbeit",
  processed: "Verarbeitet",
  error: "Fehler",
  failed: "Fehlgeschlagen",
};

function statusLabel(status: string) {
  return STATUS_LABEL[status] ?? status;
}

const ART_LABEL: Record<MaterialKind, string> = {
  aufschrieb: "Aufschrieb",
  arbeitsblatt: "Arbeitsblatt",
  loesung: "Lösung",
  sonstiges: "Sonstiges",
};

const QUELLE_LABEL: Record<string, string> = {
  ocr_local: "abgeschrieben auf diesem Rechner",
  ocr_online: "abgeschrieben vom KI-Dienst",
  pdf: "aus dem PDF",
  text: "Textdatei",
};

/** Fotos, Scans und PDFs gehen ins Abschreiben; Text wird sofort gelesen. */
const ERLAUBTE_DATEIEN = ".txt,.md,.markdown,.pdf,.png,.jpg,.jpeg,.webp";
/** Solange etwas abgeschrieben wird, alle 8 s nachsehen - eine Seite dauert etwa eine Minute. */
const NACHSEHEN_MS = 8_000;

function formatDatum(iso: string) {
  const [jahr, monat, tag] = iso.split("-");
  return `${tag}.${monat}.${jahr}`;
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

export function DocumentsView() {
  const sammlung = useDocuments();
  const suche = useAction<DocumentSearchResult>();
  const upload = useAction<FileUploadResult>();

  const [suchText, setSuchText] = useState("");
  const [datei, setDatei] = useState<File | null>(null);
  const [uploadHinweis, setUploadHinweis] = useState<string | null>(null);
  const [artFehler, setArtFehler] = useState<string | null>(null);

  const { reload } = sammlung;
  const wartet = sammlung.items.some((dokument) => dokument.status === "queued");
  useEffect(() => {
    if (!wartet) {
      return;
    }
    const timer = window.setInterval(() => void reload(), NACHSEHEN_MS);
    return () => window.clearInterval(timer);
  }, [wartet, reload]);

  async function artSetzen(id: number, art: MaterialKind) {
    try {
      await updateDocumentMaterial(id, { material_kind: art });
      setArtFehler(null);
      await reload();
    } catch {
      setArtFehler("Die Art ließ sich nicht speichern.");
    }
  }

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
          Lade Unterlagen hoch und durchsuche sie; Fragen dazu beantwortet der Assistent. Ohne verbundenen KI-Dienst wird aus den Treffern
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
                  Text, PDF und Fotos (JPG, PNG, WebP). Fotos und Scans – auch handschriftliche Aufschriebe – schreibt das Bildmodell im
                  Hintergrund ab; die Art (Aufschrieb, Arbeitsblatt, Lösung) erkennt der Lernraum selbst, und du kannst sie korrigieren. Ein
                  ganzer Ordner lässt sich unter <span className="font-semibold">Einstellungen</span> über eine Dokumentverbindung indexieren.
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
                  {artFehler ? (
                    <li>
                      <FormError message={artFehler} />
                    </li>
                  ) : null}
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
                          <select
                            aria-label={`Art von ${dokument.filename}`}
                            value={dokument.material_kind ?? ""}
                            onChange={(event) => void artSetzen(dokument.id, event.target.value as MaterialKind)}
                            className="h-6 rounded-[5px] border border-border bg-background px-1.5 text-xs"
                          >
                            {dokument.material_kind ? null : <option value="">Art …</option>}
                            {(Object.keys(ART_LABEL) as MaterialKind[]).map((art) => (
                              <option key={art} value={art}>
                                {ART_LABEL[art]}
                              </option>
                            ))}
                          </select>
                        </div>
                        {dokument.material_topic || dokument.material_date ? (
                          <p className="mt-1 text-[13px]">
                            {dokument.material_topic}
                            {dokument.material_topic && dokument.material_date ? <span className="text-muted-foreground"> · </span> : null}
                            {dokument.material_date ? <span className="text-muted-foreground">{formatDatum(dokument.material_date)}</span> : null}
                          </p>
                        ) : null}
                        {dokument.status === "needs_model" ? (
                          <p className="mt-1 text-xs text-muted-foreground">
                            Für Fotos und Scans lädst du unter{" "}
                            <Link href="/ki" className="underline underline-offset-2">
                              KI &amp; Modelle
                            </Link>{" "}
                            das Bildmodell Qwen2.5-VL. Danach wird es automatisch abgeschrieben.
                          </p>
                        ) : null}
                        <p className="mt-1 text-xs font-semibold text-muted-foreground">
                          {dokument.source === "upload" ? "hochgeladen" : `aus ${dokument.source}`}
                          <span className="text-border"> · </span>
                          {dokument.processed_at ? `verarbeitet ${formatDateTime(dokument.processed_at)}` : `angelegt ${formatDateTime(dokument.created_at)}`}
                        </p>
                        {dokument.text_source ? (
                          <p className="mt-0.5 text-xs text-muted-foreground">{QUELLE_LABEL[dokument.text_source] ?? dokument.text_source}</p>
                        ) : dokument.mime_type ? (
                          <p className="mt-0.5 text-xs text-muted-foreground">{dokument.mime_type}</p>
                        ) : null}
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
            <CardTitle>Frag den Assistenten</CardTitle>
            <CardDescription className="mt-1">Fragen zu deinen Unterlagen beantwortet jetzt der Assistent – mit Quellen, Gedächtnis und Sprache.</CardDescription>
          </CardHeader>
          <CardContent>
            <Link href="/" className={buttonVariants({ variant: "outline" })}>
              <MessageSquareText aria-hidden="true" />
              Zum Assistenten
            </Link>
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
        accept={ERLAUBTE_DATEIEN}
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
