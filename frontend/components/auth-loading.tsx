"use client";

import { LoaderCircle, Sparkles } from "lucide-react";

export function AuthLoading() {
  return (
    <main className="app-surface flex min-h-screen items-center justify-center px-6">
      <div className="flex flex-col items-center gap-4 text-center">
        <span className="flex h-14 w-14 animate-pulse items-center justify-center bg-primary text-primary-foreground">
          <Sparkles className="h-6 w-6" aria-hidden="true" />
        </span>
        <LoaderCircle className="h-5 w-5 animate-spin text-primary" aria-label="Session wird geprüft" />
      </div>
    </main>
  );
}
