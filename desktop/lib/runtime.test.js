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

test("ohne Trainer-Python bleibt das Training aus", () => {
  assert.deepEqual(runtime.trainerEnv({ userDataDir: tempDir(), packaged: false, resourcesPath: "", repoRoot: "/repo", env: {} }), {});
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
