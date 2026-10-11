"use client";

import { useEffect, type ReactNode } from "react";
import { ThemeProvider, useTheme } from "next-themes";
import { DesktopBridge } from "@/components/desktop-bridge";
import { NotificationBridge } from "@/components/notification-bridge";
import { ServiceWorkerRegister } from "@/components/service-worker-register";
import { SetupGate } from "@/components/setup-gate";
import { getLocale } from "@/lib/i18n";

/**
 * `<html lang>` folgt der Oberflaechensprache. Das Layout rendert auf dem Server
 * immer `de` - die gewaehlte Sprache kennt erst der Browser.
 */
function LanguageSync() {
  useEffect(() => {
    document.documentElement.lang = getLocale();
  }, []);
  return null;
}

function ThemeColorSync() {
  const { resolvedTheme } = useTheme();

  useEffect(() => {
    if (!resolvedTheme) {
      return;
    }
    const color = resolvedTheme === "dark" ? "#0a0a0a" : "#ffffff";
    const metas = Array.from(document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]'));
    if (metas.length === 0) {
      const meta = document.createElement("meta");
      meta.name = "theme-color";
      meta.content = color;
      document.head.appendChild(meta);
      return;
    }
    for (const meta of metas) {
      meta.content = color;
      meta.removeAttribute("media");
    }
  }, [resolvedTheme]);

  return null;
}

export function AppProviders({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
      <ThemeColorSync />
      <LanguageSync />
      <DesktopBridge />
      <ServiceWorkerRegister />
      {/*
        Die Erinnerungen laufen ueber die **Seite**, nicht ueber das Dashboard:
        die Bruecke holt sie im Hintergrund ab, und der Worker zeigt sie als
        Systemmeldung, wenn der Tab gerade nichts anzeigen kann.
      */}
      <NotificationBridge />
      {/* Die Schranke steht vor den Seiten, nicht vor den Bruecken: Theme und
          Fensterrahmen sollen auch auf der Einrichtungsseite stimmen. */}
      <SetupGate>{children}</SetupGate>
    </ThemeProvider>
  );
}
