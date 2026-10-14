"use client";

import { useEffect } from "react";
import Link from "next/link";
import { AlertTriangle, Home, RefreshCw } from "lucide-react";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { t } from "@/lib/i18n";

/**
 * Deutsche Fehlerseite fuer Fehler in den Routen.
 *
 * Ohne diese Datei zeigt Next.js seine eigene Seite - englisch, im
 * Next-Design, ohne Navigation. In einer deutschen App ist das der haeufigste
 * sichtbare Fehlerzustand ueberhaupt, also gehoert er nicht der Bibliothek.
 */
export default function RouteError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    // In Produktion ist das die einzige Stelle, an der die Serverfehlermeldung
    // sichtbar wird.
    console.error(error);
  }, [error]);

  return (
    <div className="app-surface flex min-h-screen items-center justify-center bg-background px-4 text-foreground">
      <Card className="w-full max-w-xl">
        <CardContent className="flex flex-col items-center px-6 py-12 text-center sm:px-10">
          <span className="mb-5 flex h-14 w-14 items-center justify-center border border-border bg-muted">
            <AlertTriangle className="h-7 w-7" aria-hidden="true" />
          </span>
          <h1 className="text-xl font-semibold tracking-tight">{t("Hier ist etwas schiefgelaufen")}</h1>
          <p className="mt-2 max-w-md text-sm leading-6 text-muted-foreground">
            {t("Dein Slearn konnte diese Seite gerade nicht anzeigen. Deine Daten sind davon nicht betroffen.")}
          </p>
          {error.digest ? <p className="mt-3 text-xs text-muted-foreground">{t("Fehlercode:")}{" "}{error.digest}</p> : null}
          <div className="mt-7 flex flex-col gap-2 sm:flex-row">
            <Button type="button" onClick={reset}>
              <RefreshCw aria-hidden="true" />
              {t("Erneut versuchen")}
            </Button>
            <Link href="/" className={cn(buttonVariants({ variant: "outline" }), "w-full sm:w-auto")}>
              <Home aria-hidden="true" />
              {t("Zur Übersicht")}
            </Link>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
