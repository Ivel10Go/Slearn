"use client";

import { useEffect, useState } from "react";
import { FolderOpen, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  describeUpdateStatus,
  getDesktopBridge,
  type DesktopBridge,
  type DesktopSettings,
  type DesktopUpdateStatus,
} from "@/lib/desktop";

/**
 * Was nur die Desktop-App kann: Datenordner, Autostart, Infobereich, Updates.
 *
 * Nur in der Desktop-App sichtbar: dort gibt es keinen Server und kein
 * `pg_dump`, die ganze Lernhistorie ist ein Ordner. Wer sie sichern oder auf
 * einen neuen Rechner mitnehmen will, muss diesen Ordner finden koennen - bei
 * geschlossener App kopieren reicht als Sicherung.
 */
export function DesktopCard() {
  const [bridge, setBridge] = useState<DesktopBridge | null>(null);
  const [settings, setSettings] = useState<DesktopSettings | null>(null);
  const [update, setUpdate] = useState<DesktopUpdateStatus | null>(null);
  const [checking, setChecking] = useState(false);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      const found = getDesktopBridge();
      setBridge(found);
      void found?.getSettings?.().then(setSettings);
      void found?.getUpdateStatus?.().then(setUpdate);
    });
    return () => window.cancelAnimationFrame(frame);
  }, []);

  if (!bridge) {
    return null;
  }

  async function autostart(enabled: boolean) {
    const result = await bridge?.setOpenAtLogin?.(enabled);
    if (result) {
      setSettings((previous) => (previous ? { ...previous, openAtLogin: result.openAtLogin } : previous));
    }
  }

  async function infobereich(enabled: boolean) {
    const result = await bridge?.setCloseToTray?.(enabled);
    if (result) {
      setSettings((previous) => (previous ? { ...previous, closeToTray: result.closeToTray } : previous));
    }
  }

  async function suchen() {
    setChecking(true);
    try {
      const result = await bridge?.checkForUpdates?.();
      if (result) {
        setUpdate(result);
      }
    } finally {
      setChecking(false);
    }
  }

  const updateText = describeUpdateStatus(update);
  const mac = bridge.platform === "darwin";

  return (
    <Card>
      <CardHeader>
        <CardTitle>Desktop-App</CardTitle>
        <CardDescription>
          Datenbank, Dokumente und Modelle liegen in einem Ordner. Zum Sichern Lernraum beenden und den Ordner kopieren.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="break-all rounded-md bg-muted px-3 py-2 font-mono text-xs text-muted-foreground">{bridge.dataFolder}</p>
        <Button type="button" variant="outline" size="sm" onClick={() => void bridge.openDataFolder()}>
          <FolderOpen aria-hidden="true" />
          Ordner öffnen
        </Button>

        {settings ? (
          <div className="space-y-2 border-t border-border pt-4 text-[13px]">
            <label className="flex items-start gap-2">
              <input
                type="checkbox"
                className="mt-1"
                checked={settings.openAtLogin}
                onChange={(event) => void autostart(event.target.checked)}
              />
              <span>
                Beim Anmelden starten
                <span className="block text-xs text-muted-foreground">
                  Ohne Fenster, nur {mac ? "in der Menüleiste" : "im Infobereich"} - damit Erinnerungen auch ohne geöffnete App kommen.
                </span>
              </span>
            </label>
            {mac ? null : (
              <label className="flex items-start gap-2">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={settings.closeToTray}
                  onChange={(event) => void infobereich(event.target.checked)}
                />
                <span>
                  Beim Schließen im Infobereich weiterlaufen
                  <span className="block text-xs text-muted-foreground">Beenden dann über das Symbol im Infobereich.</span>
                </span>
              </label>
            )}
          </div>
        ) : null}

        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
          <div className="text-xs text-muted-foreground">
            {bridge.version ? <span>Version {bridge.version}</span> : null}
            {updateText ? <span className="block">{updateText}</span> : null}
          </div>
          {settings?.updatesSupported && bridge.checkForUpdates ? (
            <Button type="button" variant="outline" size="sm" onClick={() => void suchen()} disabled={checking}>
              <RefreshCw aria-hidden="true" />
              Nach Updates suchen
            </Button>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}
