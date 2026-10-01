import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Lernraum – Schulassistent",
    short_name: "Lernraum",
    description: "Dein persönlicher, intelligenter Lernraum.",
    start_url: "/",
    scope: "/",
    display: "standalone",
    orientation: "portrait-primary",
    // Muss zu app/globals.css und app/layout.tsx passen, sonst steht der
    // installierte PWA-Splash blau neben einer schwarz-weißen App.
    background_color: "#fafafa",
    theme_color: "#fafafa",
    categories: ["education", "productivity"],
    icons: [
      // "any" und "maskable" getrennt: ein Icon mit beiden Zwecken wird auf
      // Android je nach Launcher beschnitten oder mit Rand gezeigt.
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png", purpose: "maskable" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
