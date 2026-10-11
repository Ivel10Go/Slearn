import type { NextConfig } from "next";

const isProduction = process.env.NODE_ENV === "production";

// GitHub Pages: `STATIC_EXPORT=true` baut nach `out/` ohne Server. Dann gibt es
// weder die Route `/api/waitlist` (der Pages-Workflow entfernt `app/api` vor dem
// Bau) noch Antwort-Header – die CSP steht stattdessen als <meta> im Layout,
// und die Warteliste geht an die Edge Function (`NEXT_PUBLIC_WAITLIST_URL`).
// `BASE_PATH` ist der Repo-Pfad, z. B. `/School-assistant`.
const staticExport = process.env.STATIC_EXPORT === "true";
const basePath = process.env.BASE_PATH?.replace(/\/+$/, "") || undefined;
const waitlistUrl = process.env.NEXT_PUBLIC_WAITLIST_URL?.trim();
const waitlistOrigin = waitlistUrl ? new URL(waitlistUrl).origin : "";

// So streng wie die App: keine fremden Skripte, Schriften oder Bilder. Die
// Warteliste schickt an die eigene Herkunft (`/api/waitlist`) oder, statisch,
// an die Edge Function.
const contentSecurityPolicy = [
  "default-src 'self'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
  "object-src 'none'",
  `script-src 'self' 'unsafe-inline'${isProduction ? "" : " 'unsafe-eval'"}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data:",
  "font-src 'self' data:",
  `connect-src 'self'${waitlistOrigin ? ` ${waitlistOrigin}` : ""}${isProduction ? "" : " ws: wss:"}`,
].join("; ");

const nextConfig: NextConfig = {
  ...(staticExport ? { output: "export" as const, trailingSlash: true } : {}),
  basePath,
  env: {
    NEXT_PUBLIC_BASE_PATH: basePath ?? "",
    STATIC_EXPORT: staticExport ? "true" : "",
    // `frame-ancestors` gilt in <meta> nicht und loest nur eine Konsolenwarnung aus.
    LANDING_CSP: contentSecurityPolicy.replace("frame-ancestors 'none'; ", ""),
  },
  poweredByHeader: false,
  reactStrictMode: true,
  turbopack: {
    root: process.cwd(),
  },
  // Statisch gibt es keine Antwort-Header; Next warnt sonst beim Export.
  headers: staticExport ? undefined : async () => {
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "Content-Security-Policy", value: contentSecurityPolicy },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Permissions-Policy", value: "camera=(), geolocation=(), microphone=()" },
        ],
      },
    ];
  },
};

export default nextConfig;
