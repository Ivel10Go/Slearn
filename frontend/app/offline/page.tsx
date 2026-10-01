import Link from "next/link";
import { CloudOff, RefreshCw } from "lucide-react";
import { buttonVariants } from "@/components/ui/button";

export default function OfflinePage() {
  return (
    <main className="app-surface flex min-h-screen items-center justify-center px-6 py-12">
      <div className="w-full max-w-md text-center">
        <CloudOff className="mx-auto mb-5 h-6 w-6 text-muted-foreground" strokeWidth={1.75} aria-hidden="true" />
        <h1 className="text-[22px] font-semibold tracking-tight">Keine Verbindung</h1>
        <p className="mt-2 text-[13px] leading-5 text-muted-foreground">
          Der Lernraum ist gerade nicht erreichbar. Private Schul- und Lerndaten werden offline nicht gespeichert.
        </p>
        <div className="mt-6 flex flex-col gap-2 sm:flex-row sm:justify-center">
          <Link href="/" className={buttonVariants()}>
            <RefreshCw aria-hidden="true" />
            Erneut versuchen
          </Link>
          <Link href="/settings" className={buttonVariants({ variant: "outline" })}>
            Zu den Einstellungen
          </Link>
        </div>
      </div>
    </main>
  );
}
