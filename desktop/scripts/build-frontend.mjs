// Baut die Oberflaeche als Next-"standalone"-Server: frontend/ -> desktop/resources/frontend.
//
// `next build --webpack`, nicht Turbopack: Turbopack bricht hier reproduzierbar
// mit `ModuleGraph::from_graphs was canceled` ab (siehe AGENTS.md).
//
// Der Standalone-Ordner enthaelt `server.js` und nur die tatsaechlich
// gebrauchten node_modules - aber **nicht** `.next/static` und `public/`.
// Die muessen daneben kopiert werden, sonst laedt die Seite ohne CSS und JS.
import { spawnSync } from "node:child_process";
import { cpSync, existsSync, rmSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const desktopDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const frontendDir = path.resolve(desktopDir, "..", "frontend");
const target = path.join(desktopDir, "resources", "frontend");
const npx = process.platform === "win32" ? "npx.cmd" : "npx";

const build = spawnSync(npx, ["next", "build", "--webpack"], {
  cwd: frontendDir,
  stdio: "inherit",
  shell: process.platform === "win32",
  env: { ...process.env, NEXT_TELEMETRY_DISABLED: "1" },
});
if (build.status !== 0) {
  console.error("next build ist fehlgeschlagen.");
  process.exit(build.status ?? 1);
}

const standalone = path.join(frontendDir, ".next", "standalone");
if (!existsSync(path.join(standalone, "server.js"))) {
  console.error(`Kein Standalone-Server unter ${standalone} - steht output: "standalone" noch in next.config.ts?`);
  process.exit(1);
}

rmSync(target, { recursive: true, force: true });
cpSync(standalone, target, { recursive: true });
cpSync(path.join(frontendDir, ".next", "static"), path.join(target, ".next", "static"), { recursive: true });
cpSync(path.join(frontendDir, "public"), path.join(target, "public"), { recursive: true });
// Eine `.env` aus dem Entwicklungsordner darf nicht im Installer landen.
for (const file of [".env", ".env.local", ".env.production"]) {
  rmSync(path.join(target, file), { force: true });
}
console.log(`Oberfläche gebaut: ${target}`);
