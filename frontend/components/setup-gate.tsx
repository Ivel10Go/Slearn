"use client";

import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { ArrowRight, LoaderCircle } from "lucide-react";
import { BrandMark } from "@/components/app-shell";
import { LanguageSelect } from "@/components/language-select";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { usePathname, useRouter } from "next/navigation";
import { ApiError, completeSetup, getSession, getSetupMode } from "@/lib/api";
import { LOGIN_PATH, NEXT_PARAM, loginHref, resolveAccess, safeNextPath, type Access } from "@/lib/auth";
import { t } from "@/lib/i18n";
import { ServerModeContext } from "@/lib/use-access";

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
 * Schranke eine Anmeldeschranke: ohne Sitzung fuehrt jede Seite nach
 * `/anmelden` und nach der Anmeldung dorthin zurueck.
 *
 * Waehrend der Pruefung wird nichts gezeigt und nicht die Seite: die Seite
 * wuerde sofort ihre Daten laden, auf den 503 (oder 401) laufen und einen
 * Fehler blitzen lassen, den es eine Zehntelsekunde spaeter nicht mehr gibt.
 */
export function SetupGate({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [access, setAccess] = useState<Access | null>(null);
  const onLoginPage = pathname === LOGIN_PATH;

  useEffect(() => {
    let aktiv = true;
    void resolveAccess({ setupMode: getSetupMode, session: getSession }).then((result) => {
      if (aktiv) {
        setAccess(result);
      }
    });
    return () => {
      aktiv = false;
    };
  }, []);

  // Ohne Sitzung zur Anmeldung, mit Sitzung (oder ohne Anmeldung) weg von ihr.
  // `replace`, damit die Zuruecktaste nicht zwischen beiden hin- und herspringt.
  useEffect(() => {
    if (access?.state === "login" && !onLoginPage) {
      router.replace(loginHref(`${window.location.pathname}${window.location.search}`));
    }
    if (access?.state === "ready" && onLoginPage) {
      router.replace(safeNextPath(new URLSearchParams(window.location.search).get(NEXT_PARAM)));
    }
  }, [access, onLoginPage, router]);

  if (access === null) {
    return <div className="min-h-screen bg-background" aria-busy="true" />;
  }
  if (access.state === "setup") {
    return <Onboarding />;
  }
  if ((access.state === "login") !== onLoginPage) {
    return <div className="min-h-screen bg-background" aria-busy="true" />;
  }
  return <ServerModeContext.Provider value={access.serverMode}>{children}</ServerModeContext.Provider>;
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
      setError(submitError instanceof ApiError ? submitError.message : t("Das hat nicht geklappt. Bitte versuche es noch einmal."));
      setPending(false);
    }
  }

  return (
    <main className="flex min-h-screen flex-col bg-background">
      <div className="app-drag flex h-10 shrink-0 items-start justify-end px-4 pt-2">
        {/* Die Sprache steht vor allem anderen: die Frage nach dem Namen soll man schon lesen koennen. */}
        <LanguageSelect id="setup-sprache" className="app-no-drag h-8 w-auto" />
      </div>
      <div className="flex flex-1 items-center justify-center px-6 pb-16">
        <div className="w-full max-w-sm">
          <BrandMark className="h-9 w-9 rounded-[7px] text-lg" />
          <h1 className="mt-6 text-2xl font-semibold tracking-tight">{t("Willkommen in Slearn")}</h1>
          <p className="mt-2 text-[13px] leading-5 text-muted-foreground">
            {t("Stundenplan, Hausaufgaben, Prüfungen und Lernzeit an einem Ort. Alles bleibt auf diesem Rechner.")}
          </p>

          <form onSubmit={(event) => void submit(event)} className="mt-8 space-y-3">
            <label htmlFor="setup-name" className="block text-[13px] font-medium">
              {t("Wie sollen wir dich nennen?")}
            </label>
            <Input
              id="setup-name"
              autoFocus
              autoComplete="given-name"
              maxLength={100}
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder={t("Vorname")}
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
              {t("Los geht's")}
              {pending ? null : <ArrowRight aria-hidden="true" />}
            </Button>
          </form>

          <p className="mt-8 text-xs leading-5 text-muted-foreground">
            {t("Stundenplan aus WebUntis, Kalender und KI-Dienste kannst du später in den Einstellungen verbinden.")}
          </p>
        </div>
      </div>
    </main>
  );
}
