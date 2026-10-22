"use client";

import { useState, type FormEvent } from "react";
import { LoaderCircle, LogIn } from "lucide-react";
import { BrandMark } from "@/components/app-shell";
import { LanguageSelect } from "@/components/language-select";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { login } from "@/lib/api";
import { NEXT_PARAM, loginErrorMessage, safeNextPath } from "@/lib/auth";
import { t } from "@/lib/i18n";

/**
 * Die Anmeldung im Server-Modus (`AUTH_ENABLED=true`).
 *
 * Hierher fuehrt die Schranke in `components/setup-gate.tsx`, sobald
 * `/auth/me` ohne Sitzung antwortet. Im lokalen Modus und mit Sitzung leitet
 * dieselbe Schranke von hier weg - die Seite selbst prueft nichts.
 *
 * Das Ziel steht in `?weiter=` und wird erst beim Absenden gelesen:
 * `useSearchParams` braeuchte eine Suspense-Grenze, nur um einen Wert zu
 * kennen, den vor dem Klick niemand braucht.
 */
export default function LoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!username.trim() || !password || pending) {
      return;
    }
    setPending(true);
    setError(null);
    try {
      await login({ username: username.trim(), password });
      // Neu laden statt Zustand umschalten: die Schranke prueft dann die neue
      // Sitzung, und keine Karte hat den 401 von eben noch im Speicher.
      window.location.replace(safeNextPath(new URLSearchParams(window.location.search).get(NEXT_PARAM)));
    } catch (submitError) {
      setError(loginErrorMessage(submitError));
      setPassword("");
      setPending(false);
    }
  }

  return (
    <main className="flex min-h-screen flex-col bg-background">
      <div className="flex h-10 shrink-0 items-start justify-end px-4 pt-2">
        <LanguageSelect id="anmelden-sprache" className="h-8 w-auto" />
      </div>
      <div className="flex flex-1 items-center justify-center px-6 pb-16">
        <div className="w-full max-w-sm">
          <BrandMark className="h-9 w-9 rounded-[7px] text-lg" />
          <h1 className="mt-6 text-2xl font-semibold tracking-tight">{t("Bei Slearn anmelden")}</h1>
          <p className="mt-2 text-[13px] leading-5 text-muted-foreground">{t("Melde dich mit dem Konto an, das für diesen Server eingerichtet ist.")}</p>

          <form onSubmit={(event) => void submit(event)} className="mt-8 space-y-3" noValidate>
            <div className="space-y-1.5">
              <label htmlFor="anmelden-benutzername" className="block text-[13px] font-medium">
                {t("Benutzername")}
              </label>
              <Input
                id="anmelden-benutzername"
                name="username"
                autoFocus
                autoComplete="username"
                autoCapitalize="none"
                spellCheck={false}
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                disabled={pending}
                aria-invalid={error ? true : undefined}
                aria-describedby={error ? "anmelden-fehler" : undefined}
              />
            </div>
            <div className="space-y-1.5">
              <label htmlFor="anmelden-passwort" className="block text-[13px] font-medium">
                {t("Passwort")}
              </label>
              <Input
                id="anmelden-passwort"
                name="password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                disabled={pending}
                aria-invalid={error ? true : undefined}
                aria-describedby={error ? "anmelden-fehler" : undefined}
              />
            </div>
            {error ? (
              <p id="anmelden-fehler" className="text-xs text-foreground underline decoration-foreground/40 underline-offset-2" role="alert">
                {error}
              </p>
            ) : null}
            <Button type="submit" className="w-full" disabled={pending || !username.trim() || !password}>
              {pending ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <LogIn aria-hidden="true" />}
              {pending ? t("Wird angemeldet …") : t("Anmelden")}
            </Button>
          </form>
        </div>
      </div>
    </main>
  );
}
