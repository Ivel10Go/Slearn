// Startet das eingefrorene Backend aus resources/backend gegen eine leere
// Datenbank und prueft den Weg, den ein neuer Nutzer geht: Migrationen,
// Ersteinrichtung, Dashboard. PyInstaller meldet fehlende versteckte Importe
// nicht beim Bauen - ein `ModuleNotFoundError` kaeme sonst erst beim Nutzer.
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const runtime = require("../lib/runtime.js");

const desktopDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const dataDir = mkdtempSync(path.join(os.tmpdir(), "lernraum-smoke-"));
const port = await runtime.findFreePort();
const { command, cwd } = runtime.backendCommand({
  packaged: true,
  resourcesPath: path.join(desktopDir, "resources"),
  repoRoot: path.resolve(desktopDir, ".."),
  platform: process.platform,
});

let output = "";
const child = spawn(command, ["--port", String(port), "--watch-parent"], {
  cwd,
  env: { ...process.env, ...runtime.backendEnv({ dataDir, secrets: { secretKey: "smoke-a", integrationKey: "smoke-b" } }) },
  stdio: ["pipe", "pipe", "pipe"],
});
child.stdout.on("data", (chunk) => (output += chunk));
child.stderr.on("data", (chunk) => (output += chunk));
let alive = true;
child.once("exit", () => (alive = false));

const base = `http://127.0.0.1:${port}`;

async function expectStatus(method, url, expected, body) {
  const response = await fetch(`${base}${url}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (response.status !== expected) {
    throw new Error(`${method} ${url}: erwartet ${expected}, bekommen ${response.status} - ${await response.text()}`);
  }
  console.log(`ok  ${method} ${url} -> ${response.status}`);
  return response;
}

try {
  await runtime.waitForHttp(`${base}/health/live`, { timeoutMs: 120_000, isAlive: () => alive });
  const status = await (await expectStatus("GET", "/api/v1/setup", 200)).json();
  if (status.needs_setup !== true) {
    throw new Error("Eine frische Datenbank muesste eingerichtet werden.");
  }
  await expectStatus("POST", "/api/v1/setup", 201, { name: "Rauchtest", timezone: "Europe/Berlin" });
  await expectStatus("GET", "/api/v1/dashboard", 200);
  await expectStatus("GET", "/api/v1/greeting", 200);
  await expectStatus("GET", "/api/v1/progress/insights", 200);
  console.log("Rauchtest bestanden.");
} catch (error) {
  console.error(output);
  console.error(error instanceof Error ? error.message : error);
  process.exitCode = 1;
} finally {
  child.stdin.end();
  child.kill();
  await new Promise((resolve) => (alive ? child.once("exit", resolve) : resolve()));
  rmSync(dataDir, { recursive: true, force: true });
}
