import type { NextConfig } from "next";

const isProduction = process.env.NODE_ENV === "production";
const contentSecurityPolicy = [
  "default-src 'self'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
  "object-src 'none'",
  `script-src 'self' 'unsafe-inline'${isProduction ? "" : " 'unsafe-eval'"}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  // `media-src` stand hier vorher gar nicht, und `default-src 'self'` hat die
  // Aufnahme blockiert. Zwei getrennte Faelle, die man nicht verwechseln darf:
  //
  // 1. **Die Aufnahme** (`getUserMedia`) faellt *nicht* unter `media-src` - sie
  //    faellt unter die Permissions-Policy weiter unten. `media-src` hat sie
  //    nie behindert.
  // 2. **Die Wiedergabe** der Sprachausgabe faellt darunter: der Browser holt
  //    die WAV-Datei per `fetch` und spielt sie ueber eine `blob:`-Adresse ab.
  //    Ohne `blob:` in diesem Feld waere die Abholung in Ordnung und die
  //    Wiedergabe trotzdem blockiert - und beides sieht in der Konsole
  //    gleich aus.
  //
  // `data:` steht daneben, weil ein eingebetteter Daten-URL-Ton sonst an
  // derselben Stelle scheitert.
  "media-src 'self' blob: data:",
  `connect-src 'self'${isProduction ? "" : " ws: wss: http: https:"}`,
  "worker-src 'self' blob:",
  "manifest-src 'self'",
].join("; ");

const securityHeaders = [
  { key: "Content-Security-Policy", value: contentSecurityPolicy },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  /*
   * `microphone=()` war hier der Grund, warum die Spracheingabe im Betrieb
   * gar nicht erst beginnen konnte: ein leerer Klammer-Ausdruck erlaubt das
   * Mikrofon **nirgends**, nicht einmal derselben Herkunft. `getUserMedia`
   * scheitert dann mit einem `NotAllowedError`, bevor der Browser die
   * Nutzerfrage stellt - und kein Test faellt darueber, weil die Zeile
   * vollstaendig aussieht.
   *
   * `(self)` heisst: nur diese Herkunft darf. Ein eingebetteter Rahmen aus einer
   * fremden Herkunft bekommt es damit auch nicht, obwohl der Iframe es in
   * seiner eigenen Policy erlauben koennte - deshalb `self` und nicht `*`.
   *
   * Kamera und Standort bleiben zu. Slearn braucht sie nicht, und eine
   * abgeschaltete Berechtigung ist die einzige, die man nicht verteidigen muss.
   */
  { key: "Permissions-Policy", value: "camera=(), geolocation=(), microphone=(self)" },
  { key: "X-DNS-Prefetch-Control", value: "off" },
];

if (isProduction && process.env.ENABLE_HSTS === "true") {
  securityHeaders.push({ key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" });
}

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  reactStrictMode: true,
  turbopack: {
    root: process.cwd(),
  },
  async headers() {
    return [{ source: "/(.*)", headers: securityHeaders }];
  },
};

export default nextConfig;
