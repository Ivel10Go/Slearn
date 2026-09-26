import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import { AppProviders } from "./providers";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "Lernraum",
    template: "%s · Lernraum",
  },
  description: "Dein ruhiger, intelligenter Lernraum für Schule und Alltag.",
  applicationName: "Lernraum",
  icons: {
    icon: "/icons/icon-192.svg",
    apple: "/icons/icon-192.svg",
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#fafafa" },
    { media: "(prefers-color-scheme: dark)", color: "#0a0a0a" },
  ],
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="de" suppressHydrationWarning>
      <body>
        <AppProviders>{children}</AppProviders>
      </body>
    </html>
  );
}
