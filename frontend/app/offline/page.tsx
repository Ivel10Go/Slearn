import Link from "next/link";
import { CloudOff, RefreshCw, Sparkles } from "lucide-react";

export default function OfflinePage() {
  return (
    <main className="app-surface flex min-h-screen items-center justify-center px-6 py-12">
      <div className="w-full max-w-md text-center">
        <div className="mx-auto mb-6 flex h-16 w-16 items-center justify-center rounded-2xl bg-primary text-primary-foreground shadow-lg shadow-primary/20">
          <CloudOff className="h-8 w-8" aria-hidden="true" />
        </div>
        <p className="mb-3 text-sm font-semibold uppercase tracking-[0.2em] text-primary">Offline-Modus</p>
        <h1 className="text-3xl font-bold tracking-tight">Deine App-Schale ist bereit.</h1>
        <p className="mt-4 leading-7 text-muted-foreground">
          Die statische Oberfläche ist verfügbar. Private Schul- und Lerndaten werden offline nicht gespeichert.
        </p>
        <div className="mt-8 flex flex-col gap-3 sm:flex-row sm:justify-center">
          <Link
            href="/"
            className="inline-flex h-10 items-center justify-center gap-2 rounded-xl bg-primary px-4 text-sm font-semibold text-primary-foreground transition hover:bg-primary/90"
          >
            <RefreshCw className="h-4 w-4" aria-hidden="true" />
            Erneut versuchen
          </Link>
          <Link
            href="/login"
            className="inline-flex h-10 items-center justify-center gap-2 rounded-xl border border-border bg-card px-4 text-sm font-semibold transition hover:bg-muted"
          >
            <Sparkles className="h-4 w-4 text-primary" aria-hidden="true" />
            Zum Login
          </Link>
        </div>
      </div>
    </main>
  );
}
