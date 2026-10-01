"use client";

import { useEffect, useState } from "react";
import { FolderOpen } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getDesktopBridge, type DesktopBridge } from "@/lib/desktop";

/**
 * Wo die Daten der Desktop-App liegen.
 *
 * Nur in der Desktop-App sichtbar: dort gibt es keinen Server und kein
 * `pg_dump`, die ganze Lernhistorie ist ein Ordner. Wer sie sichern oder auf
 * einen neuen Rechner mitnehmen will, muss diesen Ordner finden koennen - bei
 * geschlossener App kopieren reicht als Sicherung.
 */
export function DesktopCard() {
  const [bridge, setBridge] = useState<DesktopBridge | null>(null);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => setBridge(getDesktopBridge()));
    return () => window.cancelAnimationFrame(frame);
  }, []);

  if (!bridge) {
    return null;
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Daten auf diesem Rechner</CardTitle>
        <CardDescription>
          Datenbank, Dokumente und Modelle liegen in einem Ordner. Zum Sichern Lernraum beenden und den Ordner kopieren.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="break-all rounded-md bg-muted px-3 py-2 font-mono text-xs text-muted-foreground">{bridge.dataFolder}</p>
        <div className="flex items-center justify-between gap-3">
          <Button type="button" variant="outline" size="sm" onClick={() => void bridge.openDataFolder()}>
            <FolderOpen aria-hidden="true" />
            Ordner öffnen
          </Button>
          {bridge.version ? <span className="text-xs text-muted-foreground">Version {bridge.version}</span> : null}
        </div>
      </CardContent>
    </Card>
  );
}
