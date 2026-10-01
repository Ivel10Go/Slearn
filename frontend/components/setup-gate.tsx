"use client";

import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { ArrowRight, LoaderCircle } from "lucide-react";
import { BrandMark } from "@/components/app-shell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ApiError, completeSetup, getSetupStatus } from "@/lib/api";

type GateState = "checking" | "needed" | "ready";

function localTimezone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || undefined;
  } catch {
    return undefined;
  }
}

/**
 * Der erste Start der Desktop-App.
 *
 * Eine frische Installation hat kein Konto, und ohne Konto antwortet jede Route
 * mit 503. Statt die Seiten das einzeln abfangen zu lassen, steht diese Schranke
 * einmal vor allem: solange `/v1/setup` "noch einzurichten" meldet, gibt es nur
 * die Frage nach dem Namen. Ein Server mit Anmeldung meldet 404 - dann ist die
 * Schranke offen und die Web-Fassung verhaelt sich wie bisher.
 *
 * Waehrend der Pruefung wird nichts gezeigt und nicht die Seite: die Seite
 * wuerde sofort ihre Daten laden, auf den 503 laufen und einen Fehler blitzen
 * lassen, den es eine Zehntelsekunde spaeter nicht mehr gibt.
 */
export function SetupGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<GateState>("checking");

  useEffect(() => {
    let aktiv = true;
    void getSetupStatus().then((needed) => {
      if (aktiv) {
        setState(needed ? "needed" : "ready");
      }
    });
    return () => {
      aktiv = false;
    };
  }, []);

  if (state === "checking") {
    return <div className="min-h-screen bg-background" aria-busy="true" />;
  }
  if (state === "needed") {
    return <Onboarding />;
  }
  return <>{children}</>;
}

function Onboarding() {
  const [name, setName] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed || pending) {
      return;
    }
    setPending(true);
    setError(null);
    try {
      await completeSetup({ name: trimmed, timezone: localTimezone() });
      // Neu laden statt Zustand umschalten: jede Karte holt ihre Daten dann
      // frisch und mit dem neuen Konto, ohne dass eine von ihnen den 503 von
      // eben noch im Speicher hat.
      window.location.replace("/");
    } catch (submitError) {
      setError(submitError instanceof ApiError ? submitError.message : "Das hat nicht geklappt. Bitte versuche es noch einmal.");
      setPending(false);
    }
  }

  return (
    <main className="flex min-h-screen flex-col bg-background">
      <div className="app-drag h-10 shrink-0" aria-hidden="true" />
      <div className="flex flex-1 items-center justify-center px-6 pb-16">
        <div className="w-full max-w-sm">
          <BrandMark className="h-9 w-9 rounded-[7px] text-lg" />
          <h1 className="mt-6 text-2xl font-semibold tracking-tight">Willkommen im Lernraum</h1>
          <p className="mt-2 text-[13px] leading-5 text-muted-foreground">
            Stundenplan, Hausaufgaben, Prüfungen und Lernzeit an einem Ort. Alles bleibt auf diesem Rechner.
          </p>

          <form onSubmit={(event) => void submit(event)} className="mt-8 space-y-3">
            <label htmlFor="setup-name" className="block text-[13px] font-medium">
              Wie sollen wir dich nennen?
            </label>
            <Input
              id="setup-name"
              autoFocus
              autoComplete="given-name"
              maxLength={100}
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Vorname"
              disabled={pending}
              aria-describedby={error ? "setup-error" : undefined}
            />
            {error ? (
              <p id="setup-error" className="text-xs text-foreground underline decoration-foreground/40 underline-offset-2" role="alert">
                {error}
              </p>
            ) : null}
            <Button type="submit" className="w-full" disabled={pending || !name.trim()}>
              {pending ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : null}
              Los geht&apos;s
              {pending ? null : <ArrowRight aria-hidden="true" />}
            </Button>
          </form>

          <p className="mt-8 text-xs leading-5 text-muted-foreground">
            Stundenplan aus WebUntis, Kalender und KI-Dienste kannst du später in den Einstellungen verbinden.
          </p>
        </div>
      </div>
    </main>
  );
}
