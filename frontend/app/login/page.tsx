"use client";

import { Suspense, useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowRight, Eye, EyeOff, LoaderCircle, LockKeyhole, Sparkles } from "lucide-react";
import { ApiError, getSession, login } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

function LoginContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const nextPath = searchParams.get("next")?.startsWith("/") && !searchParams.get("next")?.startsWith("//")
    ? searchParams.get("next") ?? "/"
    : "/";
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [status, setStatus] = useState<"checking" | "idle" | "submitting">("checking");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    void getSession()
      .then((user) => {
        if (active && user) {
          router.replace(nextPath);
        }
      })
      .catch(() => undefined)
      .finally(() => {
        if (active) {
          setStatus("idle");
        }
      });
    return () => {
      active = false;
    };
  }, [nextPath, router]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setStatus("submitting");
    try {
      await login({ username: username.trim(), password });
      router.replace(nextPath);
      router.refresh();
    } catch (submitError) {
      if (submitError instanceof ApiError && submitError.status === 401) {
        setError("Benutzername oder Passwort ist nicht korrekt.");
      } else if (submitError instanceof Error) {
        setError(submitError.message);
      } else {
        setError("Die Anmeldung ist fehlgeschlagen. Bitte versuche es erneut.");
      }
      setStatus("idle");
    }
  }

  return (
    <main className="app-surface relative flex min-h-screen items-center justify-center overflow-hidden px-4 py-8 sm:px-6">
      <div className="surface-grid pointer-events-none absolute inset-0 opacity-30" aria-hidden="true" />
      <div className="relative grid w-full max-w-5xl items-center gap-10 lg:grid-cols-[1.05fr_0.95fr] lg:gap-20">
        <section className="hidden lg:block">
          <div className="mb-8 flex items-center gap-3">
            <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-primary text-primary-foreground shadow-lg shadow-primary/20">
              <Sparkles className="h-6 w-6" aria-hidden="true" />
            </span>
            <div>
              <p className="text-sm font-extrabold tracking-tight">Lernraum</p>
              <p className="text-xs text-muted-foreground">Dein persönlicher Lerntag</p>
            </div>
          </div>
          <p className="mb-4 text-sm font-bold uppercase tracking-[0.22em] text-primary">Willkommen zurück</p>
          <h1 className="max-w-xl text-5xl font-extrabold leading-[1.05] tracking-[-0.04em] xl:text-6xl">
            Klarer denken.<br />
            <span className="text-primary">Besser lernen.</span>
          </h1>
          <p className="mt-6 max-w-lg text-lg leading-8 text-muted-foreground">
            Dein Schulalltag an einem Ort: Stundenplan, Aufgaben, Prüfungen und ein Assistenzsystem, das dich im richtigen Moment unterstützt.
          </p>
          <div className="mt-10 flex items-center gap-3 text-sm font-semibold text-muted-foreground">
            <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-secondary text-secondary-foreground">
              <LockKeyhole className="h-4 w-4" aria-hidden="true" />
            </span>
            Privat und sicher durch eine Session
          </div>
        </section>

        <Card className="border-border/80 bg-card/90 shadow-xl shadow-slate-950/5 backdrop-blur-xl">
          <CardHeader className="space-y-3 pb-4">
            <div className="mb-2 flex h-11 w-11 items-center justify-center rounded-2xl bg-primary/10 text-primary lg:hidden">
              <Sparkles className="h-5 w-5" aria-hidden="true" />
            </div>
            <div>
              <CardTitle className="text-2xl">Anmelden</CardTitle>
              <CardDescription className="mt-1">Schön, dass du da bist. Starte mit deinem Lernkonto.</CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            <form className="space-y-5" onSubmit={handleSubmit} noValidate>
              <div className="space-y-2">
                <label className="text-sm font-bold" htmlFor="username">
                  Benutzername
                </label>
                <Input
                  id="username"
                  name="username"
                  type="text"
                  autoComplete="username"
                  placeholder="z. B. lea.muster"
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                  required
                  autoFocus
                  disabled={status === "submitting"}
                />
              </div>
              <div className="space-y-2">
                <div className="flex items-center justify-between gap-3">
                  <label className="text-sm font-bold" htmlFor="password">
                    Passwort
                  </label>
                  <span className="text-xs text-muted-foreground">Sicher gespeichert</span>
                </div>
                <div className="relative">
                  <Input
                    id="password"
                    name="password"
                    type={showPassword ? "text" : "password"}
                    autoComplete="current-password"
                    placeholder="Dein Passwort"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    required
                    className="pr-11"
                    disabled={status === "submitting"}
                  />
                  <button
                    type="button"
                    className="absolute inset-y-0 right-0 flex w-11 items-center justify-center text-muted-foreground transition hover:text-foreground"
                    onClick={() => setShowPassword((visible) => !visible)}
                    aria-label={showPassword ? "Passwort verbergen" : "Passwort anzeigen"}
                  >
                    {showPassword ? <EyeOff className="h-4 w-4" aria-hidden="true" /> : <Eye className="h-4 w-4" aria-hidden="true" />}
                  </button>
                </div>
              </div>
              {error ? (
                <p className="rounded-xl border border-destructive/20 bg-destructive/10 px-3.5 py-3 text-sm font-medium text-destructive" role="alert">
                  {error}
                </p>
              ) : null}
              <Button type="submit" className="w-full" size="lg" disabled={status === "submitting"}>
                {status === "submitting" ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : null}
                {status === "submitting" ? "Anmeldung läuft …" : "Anmelden"}
                {status !== "submitting" ? <ArrowRight aria-hidden="true" /> : null}
              </Button>
            </form>
            <p className="mt-6 text-center text-xs leading-5 text-muted-foreground">
              Noch kein Zugang? <Link href="/" className="font-bold text-primary hover:underline">Zum Lernraum</Link>
            </p>
          </CardContent>
        </Card>
      </div>
    </main>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={<main className="app-surface flex min-h-screen items-center justify-center"><LoaderCircle className="h-6 w-6 animate-spin text-primary" aria-label="Wird geladen" /></main>}>
      <LoginContent />
    </Suspense>
  );
}
