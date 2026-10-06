import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import { GeistMono } from "geist/font/mono";
import { GeistSans } from "geist/font/sans";
import "./globals.css";

const description =
  "Stundenplan, Hausaufgaben, Prüfungen, Lernpläne und Noten an einem Ort – mit einem Lern-Assistenten, der auf deinem Rechner läuft. Jetzt auf die Warteliste.";

export const metadata: Metadata = {
  metadataBase: new URL(process.env.LANDING_SITE_URL ?? "http://localhost:3100"),
  title: "Lernraum – der Schulalltag an einem Ort",
  description,
  applicationName: "Lernraum",
  icons: { icon: "/icon.svg" },
  openGraph: {
    title: "Lernraum",
    description,
    type: "website",
    locale: "de_DE",
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
    { media: "(prefers-color-scheme: dark)", color: "#0a0a0a" },
  ],
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="de" className={`${GeistSans.variable} ${GeistMono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
