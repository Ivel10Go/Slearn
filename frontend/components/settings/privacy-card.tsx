"use client";

import { useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { Download, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { deleteAccount, downloadAccountExport } from "@/lib/api";
import { getDesktopBridge } from "@/lib/desktop";

const BESTAETIGUNG = "LÖSCHEN";

/**
 * Datenauskunft und Kontoloeschung (DSGVO Art. 15, 17, 20).
 *
 * Die Desktop-App kennt kein Passwort (`AUTH_ENABLED=false`), dort genuegt das
 * Bestaetigungswort. Auf einem Server verlangt das Backend zusaetzlich das
 * Passwort - das Feld erscheint deshalb nur ausserhalb der Desktop-App.
 */
export function PrivacyCard() {
  const [desktop, setDesktop] = useState(false);
  const [exportiert, setExportiert] = useState<"bereit" | "laeuft" | "fertig">("bereit");
  const [exportFehler, setExportFehler] = useState<string | null>(null);
  const [wort, setWort] = useState("");
  const [passwort, setPasswort] = useState("");
  const [loescht, setLoescht] = useState(false);
  const [loeschFehler, setLoeschFehler] = useState<string | null>(null);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => setDesktop(getDesktopBridge() !== null));
    return () => window.cancelAnimationFrame(frame);
  }, []);

  async function exportieren() {
    setExportiert("laeuft");
    setExportFehler(null);
    try {
      const { blob, filename } = await downloadAccountExport();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
      setExportiert("fertig");
    } catch (error) {
      setExportFehler(error instanceof Error ? error.message : "Der Export ist fehlgeschlagen.");
      setExportiert("bereit");
    }
  }

  async function loeschen(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoescht(true);
    setLoeschFehler(null);
    try {
      await deleteAccount(wort.trim(), desktop ? undefined : passwort);
      // Lokaler Verlauf des Assistenten und gemerkte Reiter gehoeren mit dazu.
      try {
        window.localStorage.clear();
      } catch {
        // Ohne Speicherzugriff gibt es auch nichts zu loeschen.
      }
      // Ein voller Neuladen statt `router.push`: die Einrichtungs- bzw.
      // Anmeldeseite muss ihren Zustand neu pruefen, nicht den alten behalten.
      window.history.replaceState(null, "", "/");
      window.location.reload();
    } catch (error) {
      setLoeschFehler(error instanceof Error ? error.message : "Das Konto konnte nicht gelöscht werden.");
      setLoescht(false);
    }
  }

  const bereit = wort.trim() === BESTAETIGUNG && (desktop || passwort.length > 0);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Deine Daten</CardTitle>
        <CardDescription>
          Alles, was du im Lernraum gespeichert hast, als Datei herunterladen oder endgültig löschen. Mehr dazu in der{" "}
          <Link href="/datenschutz" className="underline underline-offset-2 hover:text-foreground">
            Datenschutzerklärung
          </Link>
          .
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="space-y-2">
          <p className="text-[13px] leading-5 text-muted-foreground">
            Eine ZIP-Datei mit allen Einträgen (Fächer, Noten, Aufgaben, Notizen des Assistenten …) und deinen hochgeladenen
            Dokumenten. Zugangsdaten und Schlüssel sind nicht enthalten.
          </p>
          <div className="flex items-center gap-3">
            <Button type="button" variant="outline" size="sm" onClick={() => void exportieren()} disabled={exportiert === "laeuft"}>
              <Download aria-hidden="true" />
              {exportiert === "laeuft" ? "Wird erstellt …" : "Daten herunterladen"}
            </Button>
            {exportiert === "fertig" ? <span className="text-xs text-muted-foreground">Heruntergeladen.</span> : null}
          </div>
          {exportFehler ? (
            <p className="text-[13px] text-destructive" role="alert">
              {exportFehler}
            </p>
          ) : null}
        </div>

        <form className="space-y-3 border-t border-border pt-4" onSubmit={(event) => void loeschen(event)}>
          <p className="text-[13px] font-medium">Konto löschen</p>
          <p className="text-[13px] leading-5 text-muted-foreground">
            Löscht dein Konto mit allen Einträgen, hochgeladenen Dokumenten, Trainingsdaten und persönlichen Adaptern. Das lässt
            sich nicht rückgängig machen. Heruntergeladene Modelle und Dateien in verbundenen Ordnern bleiben erhalten.
          </p>
          <Field id="konto-loeschen-wort" label={`Zur Bestätigung „${BESTAETIGUNG}“ eingeben`}>
            {(props) => <Input {...props} value={wort} onChange={(event) => setWort(event.target.value)} autoComplete="off" />}
          </Field>
          {desktop ? null : (
            <Field id="konto-loeschen-passwort" label="Passwort">
              {(props) => (
                <Input
                  {...props}
                  type="password"
                  value={passwort}
                  onChange={(event) => setPasswort(event.target.value)}
                  autoComplete="current-password"
                />
              )}
            </Field>
          )}
          {loeschFehler ? (
            <p className="text-[13px] text-destructive" role="alert">
              {loeschFehler}
            </p>
          ) : null}
          <Button type="submit" variant="destructive" size="sm" disabled={!bereit || loescht}>
            <Trash2 aria-hidden="true" />
            {loescht ? "Wird gelöscht …" : "Konto endgültig löschen"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
