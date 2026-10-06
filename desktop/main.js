// @ts-check
"use strict";

/**
 * Lernraum als Desktop-App.
 *
 * Die App ist dieselbe Oberflaeche wie im Browser - nur dass sie ihre zwei
 * Server selbst mitbringt und startet:
 *
 *   Fenster ──http://127.0.0.1:FRONTEND──▶ Next-Server (Electrons eigenes Node)
 *                                              └─▶ http://127.0.0.1:BACKEND ──▶ FastAPI (PyInstaller) ──▶ SQLite
 *
 * Beide lauschen nur auf 127.0.0.1. Die Daten liegen im Benutzerordner der App
 * (`app.getPath("userData")/data`), getrennt vom Programm - ein Update oder eine
 * Neuinstallation laesst sie unangetastet.
 */

const { spawn } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const {
  BrowserWindow,
  Menu,
  app,
  dialog,
  ipcMain,
  nativeTheme,
  powerMonitor,
  session,
  shell,
  utilityProcess,
} = require("electron");
const runtime = require("./lib/runtime.js");

const BACKGROUND = { light: "#ffffff", dark: "#0a0a0a" };
const isMac = process.platform === "darwin";
const repoRoot = path.resolve(__dirname, "..");

// Ein anderer Datenordner fuer Tests oder einen USB-Stick. Muss vor dem ersten
// `getPath("userData")` gesetzt werden - auch Chromium legt dort sein Profil ab.
if (process.env.LERNRAUM_USER_DATA) {
  app.setPath("userData", path.resolve(process.env.LERNRAUM_USER_DATA));
}
const userDataDir = app.getPath("userData");
const dataDir = path.join(userDataDir, "data");
const logDir = path.join(userDataDir, "logs");

/** @type {import("node:child_process").ChildProcess | null} */
let backendProcess = null;
/** @type {Electron.UtilityProcess | null} */
let frontendProcess = null;
/** @type {BrowserWindow | null} */
let mainWindow = null;
/** @type {BrowserWindow | null} */
let splashWindow = null;
let appOrigin = "";
let quitting = false;
/** @type {NodeJS.Timeout | null} */
let trainingTimer = null;

function windowBackground() {
  return nativeTheme.shouldUseDarkColors ? BACKGROUND.dark : BACKGROUND.light;
}

/* ------------------------------------------------------------------ Server */

/** @param {number} port */
async function startBackend(port) {
  const secrets = runtime.loadOrCreateSecrets(path.join(userDataDir, "secrets.json"));
  fs.mkdirSync(dataDir, { recursive: true });
  const { command, args, cwd } = runtime.backendCommand({
    packaged: app.isPackaged,
    resourcesPath: process.resourcesPath,
    repoRoot,
    platform: process.platform,
    python: process.env.LERNRAUM_PYTHON,
  });
  const log = runtime.openLog(path.join(logDir, "backend.log"));
  log.write(`\n--- Start ${new Date().toISOString()} ---\n`);

  const child = spawn(command, [...args, "--port", String(port), "--watch-parent"], {
    cwd,
    env: {
      ...process.env,
      ...runtime.backendEnv({ dataDir, secrets, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone }),
      ...runtime.trainerEnv({
        userDataDir,
        packaged: app.isPackaged,
        resourcesPath: process.resourcesPath,
        repoRoot,
      }),
    },
    // stdin bleibt offen: schliesst es sich, beendet sich das Backend selbst
    // (`--watch-parent`). So bleibt nach einem Absturz der App kein Server
    // zurueck, der Port und Datenbank haelt.
    stdio: ["pipe", "pipe", "pipe"],
    windowsHide: true,
  });
  child.stdout?.pipe(log, { end: false });
  child.stderr?.pipe(log, { end: false });
  child.once("exit", (code) => {
    log.write(`--- Ende (Code ${code}) ---\n`);
    backendProcess = null;
    if (!quitting) {
      void fatal("Der Lernraum-Dienst wurde unerwartet beendet.");
    }
  });
  backendProcess = child;

  await runtime.waitForHttp(`http://127.0.0.1:${port}/health/live`, {
    // Der erste Start migriert die Datenbank, und ein eingefrorenes Python
    // entpackt beim Start; auf langsamen Rechnern dauert das.
    timeoutMs: 120_000,
    isAlive: () => backendProcess !== null,
  });
}

/**
 * @param {number} port
 * @param {number} backendPort
 */
async function startFrontend(port, backendPort) {
  const override = process.env.LERNRAUM_FRONTEND_URL;
  if (override) {
    // Entwicklung: ein laufendes `next dev` statt des gebauten Servers.
    return override.replace(/\/$/, "");
  }
  const frontendDir = app.isPackaged
    ? path.join(process.resourcesPath, "frontend")
    : path.join(__dirname, "resources", "frontend");
  const serverFile = path.join(frontendDir, "server.js");
  if (!fs.existsSync(serverFile)) {
    throw new Error(`Die Oberfläche fehlt (${serverFile}). Erst "npm run build:frontend" ausführen oder LERNRAUM_FRONTEND_URL setzen.`);
  }
  const log = runtime.openLog(path.join(logDir, "frontend.log"));
  log.write(`\n--- Start ${new Date().toISOString()} ---\n`);

  // `utilityProcess` laeuft auf Electrons eigenem Node - es braucht kein
  // installiertes Node auf dem Rechner, und der Prozess endet mit der App.
  const child = utilityProcess.fork(serverFile, [], {
    cwd: frontendDir,
    serviceName: "Lernraum Oberfläche",
    stdio: "pipe",
    env: {
      ...process.env,
      NODE_ENV: "production",
      PORT: String(port),
      HOSTNAME: "127.0.0.1",
      BACKEND_INTERNAL_URL: `http://127.0.0.1:${backendPort}`,
      NEXT_TELEMETRY_DISABLED: "1",
    },
  });
  child.stdout?.pipe(log, { end: false });
  child.stderr?.pipe(log, { end: false });
  child.once("exit", (code) => {
    log.write(`--- Ende (Code ${code}) ---\n`);
    frontendProcess = null;
    if (!quitting) {
      void fatal("Die Oberfläche wurde unerwartet beendet.");
    }
  });
  frontendProcess = child;

  const origin = `http://127.0.0.1:${port}`;
  await runtime.waitForHttp(`${origin}/offline`, { timeoutMs: 60_000, isAlive: () => frontendProcess !== null });
  return origin;
}

/* --------------------------------------------------------------- Training */

/**
 * Meldet dem Backend, ob gerade trainiert werden darf (am Netz, Rechner ruht).
 * Nur die App kennt Akku und Leerlauf; das Backend entscheidet daraus, ob es
 * eine Runde fortsetzt, beginnt oder anhaelt. Ohne Freigabe auf /ki tut es nichts.
 *
 * @param {number} port
 */
function reportTrainingConditions(port) {
  if (quitting || !backendProcess) {
    return;
  }
  const allowed = runtime.trainingAllowed({
    onBattery: powerMonitor.isOnBatteryPower(),
    idleSeconds: powerMonitor.getSystemIdleTime(),
  });
  const body = JSON.stringify({ allowed });
  const request = http.request(
    {
      host: "127.0.0.1",
      port,
      path: "/api/v1/assistant/training/conditions",
      method: "POST",
      headers: { "content-type": "application/json", "content-length": Buffer.byteLength(body) },
    },
    (response) => response.resume(),
  );
  request.setTimeout(10_000, () => request.destroy());
  // Vor der Ersteinrichtung gibt es keinen Nutzer (503) - das ist kein Fehler.
  request.once("error", () => {});
  request.end(body);
}

/** @param {number} port */
function watchTrainingConditions(port) {
  const report = () => reportTrainingConditions(port);
  trainingTimer = setInterval(report, 30_000);
  // Netzstecker gezogen oder Deckel zu: sofort anhalten, nicht erst nach 30 s.
  powerMonitor.on("on-battery", report);
  powerMonitor.on("on-ac", report);
  powerMonitor.on("suspend", report);
}

function stopServers() {
  quitting = true;
  if (trainingTimer) {
    clearInterval(trainingTimer);
    trainingTimer = null;
  }
  if (frontendProcess) {
    frontendProcess.kill();
    frontendProcess = null;
  }
  if (backendProcess) {
    backendProcess.stdin?.end();
    backendProcess.kill();
    backendProcess = null;
  }
}

/** @param {string} message */
async function fatal(message) {
  if (quitting) {
    return;
  }
  stopServers();
  splashWindow?.destroy();
  const result = await dialog.showMessageBox({
    type: "error",
    title: "Lernraum",
    message,
    detail: `Deine Daten sind nicht betroffen. Details stehen in den Protokollen:\n${logDir}`,
    buttons: ["Beenden", "Protokolle öffnen"],
    defaultId: 0,
  });
  if (result.response === 1) {
    await shell.openPath(logDir);
  }
  app.exit(1);
}

/* ---------------------------------------------------------------- Fenster */

function createSplash() {
  splashWindow = new BrowserWindow({
    width: 360,
    height: 240,
    frame: false,
    resizable: false,
    movable: true,
    show: false,
    center: true,
    backgroundColor: windowBackground(),
    webPreferences: { sandbox: true, contextIsolation: true },
  });
  void splashWindow.loadFile(path.join(__dirname, "splash.html"), {
    query: { theme: nativeTheme.shouldUseDarkColors ? "dark" : "light" },
  });
  splashWindow.once("ready-to-show", () => splashWindow?.show());
}

/** @param {string} url */
function isAppUrl(url) {
  return appOrigin !== "" && url.startsWith(`${appOrigin}/`);
}

/** @param {string} url */
function openExternal(url) {
  if (/^https?:\/\//i.test(url) || /^mailto:/i.test(url)) {
    void shell.openExternal(url);
  }
}

function createMainWindow() {
  mainWindow = new BrowserWindow({
    width: 1320,
    height: 860,
    minWidth: 960,
    minHeight: 620,
    show: false,
    title: "Lernraum",
    backgroundColor: windowBackground(),
    autoHideMenuBar: true,
    // Beide Systeme behalten ihre normale Titelleiste; sie folgt ueber
    // `nativeTheme` dem gewaehlten Design. Mit `hiddenInset` lagen die
    // Ampelknoepfe des Macs ueber der Oberflaeche - auf schmalen Fenstern ohne
    // Seitenleiste direkt ueber dem Seitenkopf.
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      spellcheck: true,
      additionalArguments: [`--lernraum-version=${app.getVersion()}`, `--lernraum-data=${dataDir}`],
    },
  });

  const contents = mainWindow.webContents;
  // Links nach draussen gehoeren in den Browser, nicht in ein zweites
  // App-Fenster ohne Adressleiste.
  contents.setWindowOpenHandler(({ url }) => {
    if (!isAppUrl(url)) {
      openExternal(url);
    }
    return { action: "deny" };
  });
  contents.on("will-navigate", (event, url) => {
    if (!isAppUrl(url)) {
      event.preventDefault();
      openExternal(url);
    }
  });

  mainWindow.once("ready-to-show", () => {
    splashWindow?.destroy();
    splashWindow = null;
    mainWindow?.show();
  });
  mainWindow.on("closed", () => {
    mainWindow = null;
  });
  void mainWindow.loadURL(`${appOrigin}/`);
}

/**
 * Berechtigungen nur fuer die eigene Oberflaeche: Mikrofon (Diktat) und
 * Mitteilungen (Erinnerungen). Alles andere - Kamera, Standort, ... - wird
 * abgelehnt, ohne zu fragen.
 */
function configurePermissions() {
  const allowed = new Set(["media", "notifications", "clipboard-sanitized-write"]);
  session.defaultSession.setPermissionRequestHandler((contents, permission, callback) => {
    callback(isAppUrl(contents.getURL()) && allowed.has(permission));
  });
  session.defaultSession.setPermissionCheckHandler((_contents, permission, origin) => {
    return origin === appOrigin && allowed.has(permission);
  });
}

function buildMenu() {
  /** @type {Electron.MenuItemConstructorOptions[]} */
  const template = [
    ...(isMac
      ? [
          {
            label: "Lernraum",
            submenu: [
              { role: /** @type {const} */ ("about"), label: "Über Lernraum" },
              { type: /** @type {const} */ ("separator") },
              { role: /** @type {const} */ ("hide"), label: "Lernraum ausblenden" },
              { role: /** @type {const} */ ("hideOthers"), label: "Andere ausblenden" },
              { role: /** @type {const} */ ("unhide"), label: "Alle einblenden" },
              { type: /** @type {const} */ ("separator") },
              { role: /** @type {const} */ ("quit"), label: "Lernraum beenden" },
            ],
          },
        ]
      : []),
    {
      label: "Bearbeiten",
      submenu: [
        { role: "undo", label: "Widerrufen" },
        { role: "redo", label: "Wiederholen" },
        { type: "separator" },
        { role: "cut", label: "Ausschneiden" },
        { role: "copy", label: "Kopieren" },
        { role: "paste", label: "Einfügen" },
        { role: "selectAll", label: "Alles auswählen" },
      ],
    },
    {
      label: "Ansicht",
      submenu: [
        { role: "reload", label: "Neu laden" },
        { type: "separator" },
        { role: "resetZoom", label: "Originalgröße" },
        { role: "zoomIn", label: "Vergrößern" },
        { role: "zoomOut", label: "Verkleinern" },
        { type: "separator" },
        { role: "togglefullscreen", label: "Vollbild" },
        ...(app.isPackaged ? [] : [{ role: /** @type {const} */ ("toggleDevTools"), label: "Entwicklerwerkzeuge" }]),
      ],
    },
    {
      label: "Fenster",
      submenu: [
        { role: "minimize", label: "Minimieren" },
        ...(isMac ? [{ role: /** @type {const} */ ("zoom"), label: "Zoomen" }] : [{ role: /** @type {const} */ ("close"), label: "Schließen" }]),
      ],
    },
    {
      label: "Hilfe",
      submenu: [
        { label: "Datenordner öffnen", click: () => void shell.openPath(dataDir) },
        { label: "Protokolle öffnen", click: () => void shell.openPath(logDir) },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

function registerIpc() {
  ipcMain.on("lernraum:set-theme", (_event, theme) => {
    if (theme === "light" || theme === "dark" || theme === "system") {
      nativeTheme.themeSource = theme;
      mainWindow?.setBackgroundColor(windowBackground());
    }
  });
  ipcMain.handle("lernraum:open-data-folder", async () => {
    await shell.openPath(dataDir);
  });
}

/* ------------------------------------------------------------------- Start */

async function start() {
  createSplash();
  buildMenu();
  registerIpc();
  try {
    const backendPort = await runtime.findFreePort();
    const frontendPort = await runtime.findFreePort(runtime.PREFERRED_FRONTEND_PORT);
    await startBackend(backendPort);
    appOrigin = await startFrontend(frontendPort, backendPort);
    watchTrainingConditions(backendPort);
  } catch (error) {
    await fatal(error instanceof Error ? error.message : "Der Lernraum konnte nicht gestartet werden.");
    return;
  }
  configurePermissions();
  createMainWindow();
}

// Eine Instanz: ein zweiter Start holt das vorhandene Fenster nach vorn, statt
// einen zweiten Server gegen dieselbe Datenbank zu starten.
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) {
        mainWindow.restore();
      }
      mainWindow.focus();
    }
  });

  app.whenReady().then(start);

  app.on("activate", () => {
    // macOS: Klick aufs Dock-Symbol bei geschlossenem Fenster.
    if (!mainWindow && appOrigin) {
      createMainWindow();
    }
  });

  app.on("window-all-closed", () => {
    // Auf dem Mac bleibt die App im Dock, wie dort ueblich; die Server laufen
    // weiter, damit das naechste Fenster sofort da ist.
    if (!isMac) {
      app.quit();
    }
  });

  app.on("before-quit", stopServers);
}
