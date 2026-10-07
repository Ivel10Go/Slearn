"use client";

import { useId, useState, type FormEvent } from "react";
import { PLATFORMS, ROLES, type WaitlistField } from "@/lib/waitlist";
import { cn } from "@/lib/utils";

type Status =
  | { kind: "idle" }
  | { kind: "sending" }
  | { kind: "done"; message: string }
  | { kind: "error"; message: string; field: WaitlistField };

const inputClass =
  "h-10 w-full rounded-md border border-input bg-background px-3 text-[14px] text-foreground placeholder:text-muted-foreground transition-colors hover:border-muted-foreground/60 focus-visible:border-foreground focus-visible:outline-none aria-[invalid=true]:border-foreground";

/**
 * Anmeldung zur Warteliste. `kompakt` zeigt nur E-Mail und Einwilligung
 * (fuer den Seitenkopf), sonst kommen Name, Rolle und Plattform dazu.
 */
export function WaitlistForm({ kompakt = false, className }: { kompakt?: boolean; className?: string }) {
  const id = useId();
  const [status, setStatus] = useState<Status>({ kind: "idle" });

  async function absenden(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const payload = {
      email: form.get("email"),
      name: form.get("name") ?? "",
      role: form.get("role") ?? "",
      platform: form.get("platform") ?? "",
      consent: form.get("consent") === "on",
      website: form.get("website") ?? "",
    };
    setStatus({ kind: "sending" });
    try {
      const response = await fetch("/api/waitlist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = (await response.json().catch(() => null)) as { message?: string; field?: WaitlistField } | null;
      if (!response.ok) {
        setStatus({
          kind: "error",
          message: data?.message ?? "Das hat nicht geklappt. Bitte versuche es noch einmal.",
          field: data?.field ?? "form",
        });
        return;
      }
      setStatus({ kind: "done", message: data?.message ?? "Danke! Du stehst auf der Warteliste." });
    } catch {
      setStatus({ kind: "error", message: "Keine Verbindung. Bitte prüfe dein Netz und versuche es erneut.", field: "form" });
    }
  }

  if (status.kind === "done") {
    return (
      <div role="status" className={cn(kompakt && "rounded-lg border border-border bg-card p-5", className)}>
        <p className="text-[15px] font-medium">Eingetragen.</p>
        <p className="mt-1 text-[13px] text-muted-foreground">{status.message}</p>
      </div>
    );
  }

  const fehler = status.kind === "error" ? status : null;
  const fehlerAn = (field: WaitlistField) => (fehler?.field === field ? true : undefined);

  return (
    <form onSubmit={absenden} noValidate className={cn("space-y-3", className)} aria-describedby={`${id}-meldung`}>
      {/* Honigtopf: fuer Menschen unsichtbar, Bots fuellen ihn aus. */}
      <div aria-hidden className="absolute -left-[9999px] h-px w-px overflow-hidden">
        <label htmlFor={`${id}-website`}>Website</label>
        <input id={`${id}-website`} name="website" type="text" tabIndex={-1} autoComplete="off" />
      </div>

      {kompakt ? null : (
        <div className="grid gap-3 sm:grid-cols-2">
          <Feld label="Name" hinweis="optional" htmlFor={`${id}-name`}>
            <input id={`${id}-name`} name="name" type="text" autoComplete="given-name" maxLength={80} className={inputClass} aria-invalid={fehlerAn("name")} />
          </Feld>
          <Feld label="Ich bin" hinweis="optional" htmlFor={`${id}-role`}>
            <select id={`${id}-role`} name="role" defaultValue="" className={inputClass} aria-invalid={fehlerAn("role")}>
              <option value="">Bitte wählen</option>
              {Object.entries(ROLES).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </Feld>
        </div>
      )}

      <div className={cn("flex gap-2", kompakt ? "flex-col sm:flex-row" : "flex-col")}>
        <div className="min-w-0 flex-1">
          {kompakt ? (
            <label htmlFor={`${id}-email`} className="sr-only">
              E-Mail-Adresse
            </label>
          ) : null}
          {kompakt ? (
            <input
              id={`${id}-email`}
              name="email"
              type="email"
              required
              autoComplete="email"
              placeholder="deine@email.de"
              className={inputClass}
              aria-invalid={fehlerAn("email")}
            />
          ) : (
            <Feld label="E-Mail-Adresse" htmlFor={`${id}-email`}>
              <input
                id={`${id}-email`}
                name="email"
                type="email"
                required
                autoComplete="email"
                placeholder="deine@email.de"
                className={inputClass}
                aria-invalid={fehlerAn("email")}
              />
            </Feld>
          )}
        </div>
        {kompakt ? <Absenden laeuft={status.kind === "sending"} /> : null}
      </div>

      {kompakt ? null : (
        <fieldset>
          <legend className="mb-1.5 text-[13px] font-medium">
            Wo möchtest du Lernraum nutzen? <span className="font-normal text-muted-foreground">optional</span>
          </legend>
          <div className="grid grid-cols-3 gap-2">
            {Object.entries(PLATFORMS).map(([value, label]) => (
              <label
                key={value}
                className="flex h-10 cursor-pointer items-center justify-center rounded-md border border-input px-2 text-center text-[13px] transition-colors hover:bg-muted has-[:checked]:border-foreground has-[:checked]:bg-foreground has-[:checked]:text-background has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-ring"
              >
                <input type="radio" name="platform" value={value} className="sr-only" />
                {label}
              </label>
            ))}
          </div>
        </fieldset>
      )}

      <label className="flex items-start gap-2.5 text-[12.5px] leading-snug text-muted-foreground">
        <input
          name="consent"
          type="checkbox"
          required
          className="mt-0.5 size-4 shrink-0 accent-foreground"
          aria-invalid={fehlerAn("consent")}
        />
        <span>
          Ihr dürft mir eine E-Mail schicken, sobald Lernraum verfügbar ist. Keine Werbung, Abmeldung jederzeit. Unter 16?
          Dann frag bitte vorher deine Eltern.
        </span>
      </label>

      {kompakt ? null : <Absenden laeuft={status.kind === "sending"} breit />}

      <p id={`${id}-meldung`} role="alert" className={cn("text-[13px] font-medium", !fehler && "sr-only")}>
        {fehler?.message ?? ""}
      </p>
    </form>
  );
}

function Absenden({ laeuft, breit = false }: { laeuft: boolean; breit?: boolean }) {
  return (
    <button
      type="submit"
      disabled={laeuft}
      className={cn(
        "inline-flex h-10 shrink-0 items-center justify-center rounded-md border border-primary bg-primary px-5 text-[14px] font-medium text-primary-foreground transition-colors hover:bg-primary/88 active:opacity-80 disabled:opacity-50",
        breit && "w-full",
      )}
    >
      {laeuft ? "Wird eingetragen …" : "Auf die Warteliste"}
    </button>
  );
}

function Feld({ label, hinweis, htmlFor, children }: { label: string; hinweis?: string; htmlFor: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={htmlFor} className="block text-[13px] font-medium">
        {label} {hinweis ? <span className="font-normal text-muted-foreground">{hinweis}</span> : null}
      </label>
      {children}
    </div>
  );
}
