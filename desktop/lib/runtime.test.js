"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const net = require("node:net");
const os = require("node:os");
const path = require("node:path");
const { test } = require("node:test");
const runtime = require("./runtime.js");

function tempDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "lernraum-desktop-"));
}

test("Schluessel werden einmal angelegt und danach wiederverwendet", () => {
  const file = path.join(tempDir(), "secrets.json");
  const first = runtime.loadOrCreateSecrets(file);
  const second = runtime.loadOrCreateSecrets(file);
  assert.ok(first.secretKey.length >= 40);
  assert.notEqual(first.secretKey, first.integrationKey);
  assert.deepEqual(second, first);
});

test("ein fehlender Schluessel wird ergaenzt, der vorhandene bleibt", () => {
  const file = path.join(tempDir(), "secrets.json");
  fs.writeFileSync(file, JSON.stringify({ integrationKey: "vorhanden" }));
  const secrets = runtime.loadOrCreateSecrets(file);
  assert.equal(secrets.integrationKey, "vorhanden");
  assert.ok(secrets.secretKey);
  assert.equal(JSON.parse(fs.readFileSync(file, "utf8")).integrationKey, "vorhanden");
});

test("die SQLite-Adresse nutzt Vorwaertsstriche", () => {
  const url = runtime.sqliteUrl(path.join(tempDir(), "lernraum.db"));
  assert.match(url, /^sqlite:\/\/\//);
  assert.ok(!url.includes("\\"));
  assert.ok(url.endsWith("/lernraum.db"));
});

test("das Backend laeuft ohne Anmeldung und mit eigener Datei", () => {
  const env = runtime.backendEnv({ dataDir: tempDir(), secrets: { secretKey: "a", integrationKey: "b" }, timezone: "Europe/Berlin" });
  assert.equal(env.AUTH_ENABLED, "false");
  assert.equal(env.APP_ENV, "desktop");
  assert.equal(env.SECRET_KEY, "a");
  assert.equal(env.INTEGRATION_ENCRYPTION_KEY, "b");
  assert.equal(env.TIMEZONE, "Europe/Berlin");
  assert.match(env.DATABASE_URL, /^sqlite:\/\/\/.*lernraum\.db$/);
});

test("einem belegten Wunschport wird ausgewichen", async () => {
  const blocker = net.createServer();
  await new Promise((resolve) => blocker.listen(0, "127.0.0.1", resolve));
  const taken = blocker.address().port;
  const port = await runtime.findFreePort(taken);
  assert.notEqual(port, taken);
  assert.ok(port > 0);
  blocker.close();
});

test("ein freier Wunschport wird genommen", async () => {
  const free = await runtime.findFreePort();
  assert.equal(await runtime.findFreePort(free), free);
});

test("Warten endet, sobald der Dienst antwortet", async () => {
  const server = http.createServer((_request, response) => response.end("ok"));
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  await runtime.waitForHttp(`http://127.0.0.1:${server.address().port}/`, { timeoutMs: 2_000 });
  server.close();
});

test("Warten bricht ab, wenn der Prozess stirbt", async () => {
  const port = await runtime.findFreePort();
  await assert.rejects(runtime.waitForHttp(`http://127.0.0.1:${port}/`, { timeoutMs: 5_000, isAlive: () => false }), /beendet/);
});

test("gepackt startet das eingefrorene Backend, sonst Python", () => {
  const packaged = runtime.backendCommand({ packaged: true, resourcesPath: "/r", repoRoot: "/repo", platform: "win32" });
  assert.ok(packaged.command.endsWith("lernraum-backend.exe"));
  const dev = runtime.backendCommand({ packaged: false, resourcesPath: "/r", repoRoot: "/repo", platform: "darwin" });
  assert.equal(dev.command, "python3");
  assert.ok(dev.args[0].endsWith("desktop_main.py"));
});

test("ohne trainer.json kennt das Backend trotzdem den Trainer-Ordner", () => {
  // Die Einrichtung auf /ki braucht die mitgelieferte Sperrdatei - auch ohne eigenes Python.
  assert.deepEqual(runtime.trainerEnv({ userDataDir: tempDir(), packaged: true, resourcesPath: "/res", repoRoot: "/repo", env: {} }), {
    TRAINER_DIR: path.join("/res", "trainer"),
  });
});

test("trainer.json kann die eingerichtete Umgebung auf ein anderes Laufwerk legen", () => {
  const dir = tempDir();
  fs.writeFileSync(path.join(dir, "trainer.json"), JSON.stringify({ env: "E:/lernraum/trainer-env", python: 42 }));
  const out = runtime.trainerEnv({ userDataDir: dir, packaged: true, resourcesPath: "/res", repoRoot: "/repo", env: {} });
  assert.equal(out.TRAINER_ENV_PATH, "E:/lernraum/trainer-env");
  assert.equal(out.TRAINER_PYTHON, undefined);
});

test("der Trainer kommt aus trainer.json, die Umgebung schlaegt die Datei", () => {
  const dir = tempDir();
  fs.writeFileSync(path.join(dir, "trainer.json"), JSON.stringify({ python: "E:/venv/python.exe", cache: "E:/hf" }));
  const ausDatei = runtime.trainerEnv({ userDataDir: dir, packaged: true, resourcesPath: "/res", repoRoot: "/repo", env: {} });
  assert.equal(ausDatei.TRAINER_PYTHON, "E:/venv/python.exe");
  assert.equal(ausDatei.TRAINER_CACHE_PATH, "E:/hf");
  assert.equal(ausDatei.TRAINER_DIR, path.join("/res", "trainer"));

  const ausUmgebung = runtime.trainerEnv({ userDataDir: dir, packaged: false, resourcesPath: "/res", repoRoot: "/repo", env: { TRAINER_PYTHON: "/py" } });
  assert.equal(ausUmgebung.TRAINER_PYTHON, "/py");
  assert.equal(ausUmgebung.TRAINER_DIR, path.join("/repo", "trainer"));
});

test("trainiert wird nur am Netz und wenn der Rechner ruht", () => {
  const ruhig = runtime.TRAINING_IDLE_SECONDS;
  assert.equal(runtime.trainingAllowed({ onBattery: false, idleSeconds: ruhig }), true);
  assert.equal(runtime.trainingAllowed({ onBattery: true, idleSeconds: ruhig * 10 }), false);
  assert.equal(runtime.trainingAllowed({ onBattery: false, idleSeconds: ruhig - 1 }), false);
});

test("Desktop-Einstellungen: Standard ohne Autostart, Aenderungen bleiben erhalten", () => {
  const file = path.join(tempDir(), "desktop-settings.json");
  assert.deepEqual(runtime.loadDesktopSettings(file), { openAtLogin: false, closeToTray: true, trayHintShown: false });
  runtime.saveDesktopSettings(file, { openAtLogin: true });
  runtime.saveDesktopSettings(file, { trayHintShown: true, unbekannt: true });
  assert.deepEqual(runtime.loadDesktopSettings(file), { openAtLogin: true, closeToTray: true, trayHintShown: true });
  fs.writeFileSync(file, "{kaputt");
  assert.equal(runtime.loadDesktopSettings(file).openAtLogin, false);
});

test("Start aus dem Autostart erkennt das Argument --hidden", () => {
  assert.equal(runtime.isHiddenLaunch(["lernraum.exe", "--hidden"]), true);
  assert.equal(runtime.isHiddenLaunch(["lernraum.exe"]), false);
});

test("vor einem Update wird die Datenbank samt WAL gesichert, beim ersten Start nicht", () => {
  const dataDir = tempDir();
  // Erster Start: keine Datenbank, nichts zu sichern - aber die Version wird gemerkt.
  assert.equal(runtime.backupBeforeUpdate({ dataDir, version: "1.0.0" }), null);
  fs.writeFileSync(path.join(dataDir, "lernraum.db"), "alt");
  fs.writeFileSync(path.join(dataDir, "lernraum.db-wal"), "wal");
  // Gleiche Version: nichts.
  assert.equal(runtime.backupBeforeUpdate({ dataDir, version: "1.0.0" }), null);
  // Neue Version: Sicherung mit beiden Dateien.
  const target = runtime.backupBeforeUpdate({ dataDir, version: "1.1.0" });
  assert.ok(target && path.basename(target).startsWith("vor-update-1.0.0-"));
  assert.equal(fs.readFileSync(path.join(target, "lernraum.db"), "utf8"), "alt");
  assert.equal(fs.readFileSync(path.join(target, "lernraum.db-wal"), "utf8"), "wal");
  assert.equal(runtime.backupBeforeUpdate({ dataDir, version: "1.1.0" }), null);
});

test("es bleiben nur die letzten Update-Sicherungen liegen", () => {
  const dataDir = tempDir();
  fs.writeFileSync(path.join(dataDir, "lernraum.db"), "x");
  for (let index = 0; index < runtime.KEEP_UPDATE_BACKUPS + 2; index += 1) {
    runtime.backupBeforeUpdate({ dataDir, version: `1.${index}.0`, now: new Date(Date.UTC(2026, 9, 1, index)) });
  }
  const left = fs.readdirSync(path.join(dataDir, "backups"));
  assert.equal(left.length, runtime.KEEP_UPDATE_BACKUPS);
});

test("der alte Datenordner Lernraum zieht nach Slearn um", () => {
  const appData = tempDir();
  fs.mkdirSync(path.join(appData, "Lernraum", "data"), { recursive: true });
  fs.writeFileSync(path.join(appData, "Lernraum", "data", "lernraum.db"), "alt");
  const target = path.join(appData, "Slearn");
  assert.equal(runtime.migrateLegacyUserData({ userDataDir: target }), target);
  assert.equal(fs.readFileSync(path.join(target, "data", "lernraum.db"), "utf8"), "alt");
  assert.equal(fs.existsSync(path.join(appData, "Lernraum")), false);
});

test("ein vorhandener neuer Datenordner wird nicht angefasst", () => {
  const appData = tempDir();
  fs.mkdirSync(path.join(appData, "Lernraum"));
  fs.mkdirSync(path.join(appData, "Slearn"));
  const target = path.join(appData, "Slearn");
  assert.equal(runtime.migrateLegacyUserData({ userDataDir: target }), target);
  assert.equal(fs.existsSync(path.join(appData, "Lernraum")), true);
});

test("ohne alten Ordner bleibt es beim neuen", () => {
  const target = path.join(tempDir(), "Slearn");
  assert.equal(runtime.migrateLegacyUserData({ userDataDir: target }), target);
});
