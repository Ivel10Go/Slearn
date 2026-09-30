"use client";

import { useEffect, type ReactNode } from "react";
import { ThemeProvider, useTheme } from "next-themes";
import { NotificationBridge } from "@/components/notification-bridge";
import { ServiceWorkerRegister } from "@/components/service-worker-register";

function ThemeColorSync() {
  const { resolvedTheme } = useTheme();

  useEffect(() => {
    if (!resolvedTheme) {
      return;
    }
    const color = resolvedTheme === "dark" ? "#0a0a0a" : "#fafafa";
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
      <ServiceWorkerRegister />
      {/*
        Die Erinnerungen laufen ueber die **Seite**, nicht ueber das Dashboard:
        die Bruecke holt sie im Hintergrund ab, und der Worker zeigt sie als
        Systemmeldung, wenn der Tab gerade nichts anzeigen kann.
      */}
      <NotificationBridge />
      {children}
    </ThemeProvider>
  );
}
