import Link from "next/link";
import { Compass, Home, Settings } from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

/**
 * Deutsche 404-Seite.
 *
 * Ohne diese Datei zeigt Next.js seine eigene - englisch, ohne Navigation, und
 * sie verlinkt nirgends hin, wo man im Lernraum weitermachen kann.
 */
export default function NotFound() {
  return (
    <div className="app-surface flex min-h-screen items-center justify-center bg-background px-4 text-foreground">
      <Card className="w-full max-w-xl">
        <CardContent className="flex flex-col items-center px-6 py-12 text-center sm:px-10">
          <span className="mb-5 flex h-14 w-14 items-center justify-center border border-border bg-muted">
            <Compass className="h-7 w-7" aria-hidden="true" />
          </span>
          <p className="text-xs font-bold uppercase tracking-[0.18em] text-muted-foreground">Fehler 404</p>
          <h1 className="mt-2 text-xl font-extrabold tracking-tight">Diese Seite gibt es nicht</h1>
          <p className="mt-2 max-w-md text-sm leading-6 text-muted-foreground">
            Vielleicht wurde sie verschoben oder der Link ist nicht mehr aktuell.
          </p>
          <div className="mt-7 flex flex-col gap-2 sm:flex-row">
            <Link href="/" className={cn(buttonVariants(), "w-full sm:w-auto")}>
              <Home aria-hidden="true" />
              Zur Übersicht
            </Link>
            <Link href="/settings" className={cn(buttonVariants({ variant: "outline" }), "w-full sm:w-auto")}>
              <Settings aria-hidden="true" />
              Zu den Einstellungen
            </Link>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
