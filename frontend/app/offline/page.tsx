import Link from "next/link";
import { CloudOff, RefreshCw, Sparkles } from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export default function OfflinePage() {
  return (
    <main className="app-surface flex min-h-screen items-center justify-center px-6 py-12">
      <div className="w-full max-w-md text-center">
        <div className="mx-auto mb-6 flex h-16 w-16 items-center justify-center bg-primary text-primary-foreground">
          <CloudOff className="h-8 w-8" aria-hidden="true" />
        </div>
        <p className="mb-3 text-sm font-semibold uppercase tracking-[0.2em] text-primary">Offline-Modus</p>
        <h1 className="text-3xl font-bold tracking-tight">Deine App-Schale ist bereit.</h1>
        <p className="mt-4 leading-7 text-muted-foreground">
          Die statische Oberfläche ist verfügbar. Private Schul- und Lerndaten werden offline nicht gespeichert.
        </p>
        <div className="mt-8 flex flex-col gap-3 sm:flex-row sm:justify-center">
          <Link href="/" className={cn(buttonVariants({ size: "sm" }), "h-10")}>
            <RefreshCw aria-hidden="true" />
            Erneut versuchen
          </Link>
          <Link href="/login" className={cn(buttonVariants({ variant: "outline", size: "sm" }), "h-10")}>
            <Sparkles className="text-primary" aria-hidden="true" />
            Zum Login
          </Link>
        </div>
      </div>
    </main>
  );
}
