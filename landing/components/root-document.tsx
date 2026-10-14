import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import { GeistMono } from "geist/font/mono";
import { GeistSans } from "geist/font/sans";
import { CONTENT, homePath, LOCALES, type Locale } from "@/lib/content";
import "@/app/globals.css";

/*
 * Gemeinsames Grundgeruest der beiden Wurzel-Layouts `app/(de)/layout.tsx` und
 * `app/en/layout.tsx`. Zwei Wurzel-Layouts statt einem, weil `<html lang>`
 * sonst auf der englischen Seite deutsch bliebe – ohne Server laesst es sich
 * nicht je Anfrage setzen.
 */

const siteUrl = (process.env.LANDING_SITE_URL ?? "http://localhost:3100").replace(/\/+$/, "");

export function localeMetadata(locale: Locale): Metadata {
  const { meta } = CONTENT[locale];
  const url = (target: Locale) => `${siteUrl}${homePath(target)}`;
  return {
    metadataBase: new URL(`${siteUrl}/`),
    title: meta.title,
    description: meta.description,
    applicationName: "Lernraum",
    icons: { icon: `${process.env.NEXT_PUBLIC_BASE_PATH ?? ""}/icon.svg` },
    alternates: {
      canonical: url(locale),
      languages: { ...Object.fromEntries(LOCALES.map((target) => [target, url(target)])), "x-default": url("de") },
    },
    openGraph: {
      title: "Lernraum",
      description: meta.description,
      type: "website",
      url: url(locale),
      locale: meta.ogLocale,
      alternateLocale: LOCALES.filter((target) => target !== locale).map((target) => CONTENT[target].meta.ogLocale),
    },
  };
}

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
    { media: "(prefers-color-scheme: dark)", color: "#0a0a0a" },
  ],
};

export function RootDocument({ locale, children }: { locale: Locale; children: ReactNode }) {
  return (
    <html lang={locale} className={`${GeistSans.variable} ${GeistMono.variable}`}>
      {/* Statisch (GitHub Pages) gibt es keine Antwort-Header; die CSP kommt dann als <meta>. */}
      {process.env.STATIC_EXPORT === "true" ? (
        // Die Regel kennt nur Layouts unter `app/`; das hier ist das Geruest beider Wurzel-Layouts.
        // eslint-disable-next-line @next/next/no-head-element
        <head>
          <meta httpEquiv="Content-Security-Policy" content={process.env.LANDING_CSP} />
        </head>
      ) : null}
      <body>{children}</body>
    </html>
  );
}
