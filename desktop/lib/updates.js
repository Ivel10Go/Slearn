// @ts-check
"use strict";

/**
 * Automatische Updates ueber GitHub-Releases (electron-updater).
 *
 * Ablauf: beim Start nach 30 s und danach alle sechs Stunden nachsehen, ein
 * neues Release im Hintergrund laden und erst fragen, wenn es fertig ist.
 * Installiert wird beim Neustart - auf Wunsch sofort, sonst beim naechsten
 * Beenden. Die Lerndaten liegen im Benutzerordner und werden vom Installer
 * nicht beruehrt; vor der ersten Migration der neuen Version sichert
 * `runtime.backupBeforeUpdate()` die Datenbank.
 *
 * Nur in der gepackten App und nur, wenn `LERNRAUM_DISABLE_UPDATES` nicht
 * gesetzt ist. Ohne Netz ist ein gescheiterter Abruf kein Fehler fuer die
 * Nutzerin, er landet nur im Protokoll. macOS installiert Updates nur fuer
 * signierte Apps; eine unsignierte meldet das beim Pruefen von Hand.
 */

const path = require("node:path");
const { app, dialog } = require("electron");
const runtime = require("./runtime.js");
const { text } = require("./texts.js");

const FIRST_CHECK_MS = 30_000;
const CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000;

/** @type {import("electron-updater").AppUpdater | null} */
let updater = null;
/** @type {{ state: "idle" | "checking" | "available" | "downloaded" | "current" | "error", version?: string, message?: string }} */
let current = { state: "idle" };
/** @type {() => Electron.BrowserWindow | null} */
let windowRef = () => null;
/** @type {() => Promise<void>} */
let prepare = async () => {};

function supported() {
  return app.isPackaged && !process.env.LERNRAUM_DISABLE_UPDATES;
}

function status() {
  return { ...current, supported: supported(), version: current.version, appVersion: app.getVersion() };
}

/** @param {string} message */
function friendly(message) {
  if (/code signature|not signed|signature/i.test(message)) {
    return text("updatesUnsigned");
  }
  if (/net::|ENOTFOUND|ECONNREFUSED|ETIMEDOUT|EAI_AGAIN|getaddrinfo/i.test(message)) {
    return text("updatesOffline");
  }
  return text("updatesFailed");
}

async function askToRestart() {
  const window = windowRef();
  const options = {
    type: /** @type {const} */ ("info"),
    title: text("updateReadyTitle"),
    message: text("updateReady", { version: current.version ?? "" }).replace("  ", " "),
    detail: text("updateReadyDetail"),
    buttons: [text("restartNow"), text("later")],
    defaultId: 0,
    cancelId: 1,
  };
  const result = window ? await dialog.showMessageBox(window, options) : await dialog.showMessageBox(options);
  if (result.response === 0) {
    await install();
  }
}

async function install() {
  if (!updater || current.state !== "downloaded") {
    return;
  }
  await prepare();
  // isSilent=false: der Installer zeigt seinen Fortschritt; forceRunAfter=true:
  // die App startet danach wieder.
  updater.quitAndInstall(false, true);
}

/**
 * @param {{ manual?: boolean }} [options]
 */
async function check({ manual = false } = {}) {
  if (!updater) {
    if (manual) {
      await dialog.showMessageBox({
        type: "info",
        title: text("updatesTitle"),
        message: text("updatesInstalledOnly"),
      });
    }
    return status();
  }
  if (current.state === "downloaded") {
    if (manual) {
      await askToRestart();
    }
    return status();
  }
  try {
    const result = await updater.checkForUpdates();
    if (manual && current.state === "current") {
      await dialog.showMessageBox({
        type: "info",
        title: text("updatesTitle"),
        message: text("upToDateVersion", { version: app.getVersion() }),
      });
    }
    if (result === null && manual) {
      current = { state: "current" };
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    current = { state: "error", message: friendly(message) };
    if (manual) {
      await dialog.showMessageBox({ type: "warning", title: text("updatesTitle"), message: current.message ?? "" });
    }
  }
  return status();
}

/**
 * @param {{ logDir: string, getWindow: () => Electron.BrowserWindow | null, beforeInstall: () => Promise<void> }} options
 */
function start({ logDir, getWindow, beforeInstall }) {
  windowRef = getWindow;
  prepare = beforeInstall;
  if (!supported()) {
    return;
  }
  const { autoUpdater } = require("electron-updater");
  const log = runtime.openLog(path.join(logDir, "updates.log"));
  /** @param {string} level */
  const write = (level) => (/** @type {unknown} */ message) => log.write(`${new Date().toISOString()} ${level} ${String(message)}\n`);
  autoUpdater.logger = { info: write("INFO"), warn: write("WARN"), error: write("ERROR"), debug: () => {} };
  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = true;
  autoUpdater.allowPrerelease = false;
  autoUpdater.on("checking-for-update", () => {
    current = { state: "checking" };
  });
  autoUpdater.on("update-available", (info) => {
    current = { state: "available", version: info.version };
  });
  autoUpdater.on("update-not-available", () => {
    current = { state: "current" };
  });
  autoUpdater.on("error", (error) => {
    current = { state: "error", message: friendly(error instanceof Error ? error.message : String(error)) };
  });
  autoUpdater.on("update-downloaded", (info) => {
    current = { state: "downloaded", version: info.version };
    void askToRestart();
  });
  updater = autoUpdater;
  setTimeout(() => void check(), FIRST_CHECK_MS);
  setInterval(() => void check(), CHECK_INTERVAL_MS);
}

module.exports = { check, install, start, status, supported };
