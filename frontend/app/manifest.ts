import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Slearn – Schulassistent",
    short_name: "Slearn",
    description: "Dein persönlicher, intelligenter Slearn.",
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
      {
        src: "/icons/icon-192.png",
        sizes: "192x192",
        type: "image/png",
        purpose: "maskable",
      },
      {
        src: "/icons/icon-512.png",
        sizes: "512x512",
        type: "image/png",
        purpose: "maskable",
      },
    ],
  };
}
