// @ts-check
"use strict";

/**
 * Die Teile der Desktop-App, die nichts mit Fenstern zu tun haben: Ports,
 * Schluessel, Umgebung des Backends, Warten auf einen Dienst. Getrennt von
 * `main.js`, damit sie sich mit `node --test` ohne Electron pruefen lassen.
 */

const crypto = require("node:crypto");
const fs = require("node:fs");
const http = require("node:http");
const net = require("node:net");
const path = require("node:path");

/**
 * Fester Wunschport fuer die Oberflaeche. Fest, weil die Herkunft
 * (`http://127.0.0.1:PORT`) der Schluessel fuer alles ist, was der Browser
 * speichert: gewaehltes Design, Service Worker, Mikrofon-Freigabe. Mit einem
 * Zufallsport begaenne jeder Start bei null. Nur wenn er belegt ist, wird
 * ausgewichen.
 */
const PREFERRED_FRONTEND_PORT = 47821;

/** @param {number} port */
function isPortFree(port) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.once("error", () => resolve(false));
    server.once("listening", () => server.close(() => resolve(true)));
    server.listen(port, "127.0.0.1");
  });
}

/** @returns {Promise<number>} */
function randomFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : 0;
      server.close(() => resolve(port));
    });
  });
}

/**
 * @param {number | null} [preferred]
 * @returns {Promise<number>}
 */
async function findFreePort(preferred = null) {
  if (preferred && (await isPortFree(preferred))) {
    return preferred;
  }
  return randomFreePort();
}

/**
 * Laedt die zwei Schluessel des Backends oder legt sie beim ersten Start an.
 *
 * `INTEGRATION_ENCRYPTION_KEY` verschluesselt die gespeicherten Zugangsdaten
 * (WebUntis, KI-Dienst). Geht er verloren, sind sie nicht mehr lesbar - die
 * Datei wird deshalb nie ueberschrieben, nur ergaenzt. Sie liegt im selben
 * Benutzerordner wie die Datenbank und ist damit genauso geschuetzt wie diese.
 *
 * @param {string} file
 * @returns {{ secretKey: string, integrationKey: string }}
 */
function loadOrCreateSecrets(file) {
  /** @type {{ secretKey?: string, integrationKey?: string }} */
  let stored = {};
  if (fs.existsSync(file)) {
    stored = JSON.parse(fs.readFileSync(file, "utf8"));
  }
  const secrets = {
    secretKey: stored.secretKey || crypto.randomBytes(32).toString("base64url"),
    integrationKey: stored.integrationKey || crypto.randomBytes(32).toString("base64url"),
  };
  if (secrets.secretKey !== stored.secretKey || secrets.integrationKey !== stored.integrationKey) {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, JSON.stringify(secrets, null, 2), { encoding: "utf8", mode: 0o600 });
  }
  return secrets;
}

/**
 * SQLAlchemy-Adresse fuer eine Datei. Unter Windows mit Vorwaertsstrichen:
 * `sqlite:///C:/Users/...` versteht SQLAlchemy, `C:\Users` nicht verlaesslich.
 *
 * @param {string} file
 */
function sqliteUrl(file) {
  return `sqlite:///${path.resolve(file).split(path.sep).join("/")}`;
}

/**
 * @param {{ dataDir: string, secrets: { secretKey: string, integrationKey: string }, timezone?: string }} options
 * @returns {Record<string, string>}
 */
function backendEnv({ dataDir, secrets, timezone }) {
  /** @type {Record<string, string>} */
  const env = {
    APP_ENV: "desktop",
    DATABASE_URL: sqliteUrl(path.join(dataDir, "lernraum.db")),
    SECRET_KEY: secrets.secretKey,
    INTEGRATION_ENCRYPTION_KEY: secrets.integrationKey,
    // Eine Person an diesem Rechner: kein Login. Das Konto entsteht in der
    // Ersteinrichtung (`/api/v1/setup`), nicht aus ADMIN_*-Variablen.
    AUTH_ENABLED: "false",
    BOOTSTRAP_ON_STARTUP: "false",
    DEMO_MODE: "false",
    DOCUMENT_STORAGE_PATH: path.join(dataDir, "documents"),
    MODEL_STORAGE_PATH: path.join(dataDir, "models"),
    // Geladene Modelle laufen ohne Zusatzinstallation: das Backend holt
    // llama-server, whisper-server und piper (feste Version + Pruefsumme)
    // und startet sie bei Bedarf.
    LOCAL_RUNTIMES_ENABLED: "true",
    // Beide Laeufe tun nur etwas, wenn etwas eingerichtet ist: ohne
    // WebUntis-Zugang gibt es keinen Abruf, ohne Termine keine Erinnerung.
    WEBUNTIS_SYNC_ENABLED: "true",
    REMINDER_SCHEDULER_ENABLED: "true",
    LOG_LEVEL: "WARNING",
    PYTHONUNBUFFERED: "1",
    PYTHONIOENCODING: "utf-8",
  };
  if (timezone) {
    env.TIMEZONE = timezone;
  }
  return env;
}

/**
 * Wartet, bis eine Adresse mit einem Status unter 500 antwortet.
 *
 * @param {string} url
 * @param {{ timeoutMs?: number, intervalMs?: number, isAlive?: () => boolean }} [options]
 * @returns {Promise<void>}
 */
function waitForHttp(url, { timeoutMs = 90_000, intervalMs = 250, isAlive = () => true } = {}) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const retry = () => {
      if (Date.now() > deadline) {
        reject(new Error(`${url} hat nicht rechtzeitig geantwortet.`));
        return;
      }
      setTimeout(attempt, intervalMs);
    };
    const attempt = () => {
      if (!isAlive()) {
        reject(new Error(`Der Dienst hinter ${url} wurde beendet, bevor er bereit war.`));
        return;
      }
      const request = http.get(url, (response) => {
        response.resume();
        if ((response.statusCode ?? 500) < 500) {
          resolve();
        } else {
          retry();
        }
      });
      request.setTimeout(2_000, () => request.destroy());
      request.once("error", retry);
    };
    attempt();
  });
}

/**
 * Wie das Backend gestartet wird.
 *
 * Gepackt: das PyInstaller-Programm aus `resources/backend`. In der
 * Entwicklung: `python desktop_main.py` direkt aus `backend/`, damit eine
 * Aenderung am Backend ohne neuen PyInstaller-Lauf sichtbar ist.
 *
 * @param {{ packaged: boolean, resourcesPath: string, repoRoot: string, platform: NodeJS.Platform, python?: string }} options
 */
function backendCommand({ packaged, resourcesPath, repoRoot, platform, python }) {
  if (packaged) {
    const executable = platform === "win32" ? "lernraum-backend.exe" : "lernraum-backend";
    const dir = path.join(resourcesPath, "backend");
    return { command: path.join(dir, executable), args: [], cwd: dir };
  }
  const backendDir = path.join(repoRoot, "backend");
  return {
    command: python || (platform === "win32" ? "python" : "python3"),
    args: [path.join(backendDir, "desktop_main.py")],
    cwd: backendDir,
  };
}

/**
 * Haengt an eine Logdatei an und beginnt neu, wenn sie zu gross wird - eine
 * App, die monatelang laeuft, soll den Datentraeger nicht mit Logs fuellen.
 *
 * @param {string} file
 * @param {number} [maxBytes]
 */
function openLog(file, maxBytes = 5 * 1024 * 1024) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  try {
    if (fs.statSync(file).size > maxBytes) {
      fs.renameSync(file, `${file}.1`);
    }
  } catch {
    // Keine Datei: nichts zu rotieren.
  }
  return fs.createWriteStream(file, { flags: "a" });
}

/** Erst nach so vielen Sekunden ohne Eingabe darf trainiert werden. */
const TRAINING_IDLE_SECONDS = 120;

/**
 * Wo der Trainer liegt. Das Trainer-Python (torch mit CUDA, mehrere GB) liefert
 * die App nicht mit; es steht in `trainer.json` im Benutzerordner
 * (`{"python": "...", "cache": "..."}`) oder in `TRAINER_PYTHON`. Ohne beides
 * bleibt das Training aus, und das Backend sagt das auf `/ki`.
 *
 * @param {{ userDataDir: string, packaged: boolean, resourcesPath: string, repoRoot: string, env?: NodeJS.ProcessEnv }} options
 * @returns {Record<string, string>}
 */
function trainerEnv({ userDataDir, packaged, resourcesPath, repoRoot, env = process.env }) {
  /** @type {{ python?: unknown, cache?: unknown }} */
  let file = {};
  try {
    file = JSON.parse(fs.readFileSync(path.join(userDataDir, "trainer.json"), "utf8"));
  } catch {
    // Keine oder kaputte Datei: dann nur die Umgebung.
  }
  const python = env.TRAINER_PYTHON || (typeof file.python === "string" ? file.python : "");
  if (!python) {
    return {};
  }
  /** @type {Record<string, string>} */
  const out = {
    TRAINER_PYTHON: python,
    TRAINER_DIR: env.TRAINER_DIR || (packaged ? path.join(resourcesPath, "trainer") : path.join(repoRoot, "trainer")),
  };
  const cache = env.TRAINER_CACHE_PATH || (typeof file.cache === "string" ? file.cache : "");
  if (cache) {
    out.TRAINER_CACHE_PATH = cache;
  }
  return out;
}

/**
 * Darf gerade trainiert werden? Nur am Netz und wenn niemand den Rechner benutzt -
 * Training soll den Akku nicht leeren und die Arbeit nicht ausbremsen.
 *
 * @param {{ onBattery: boolean, idleSeconds: number }} state
 */
function trainingAllowed({ onBattery, idleSeconds }) {
  return !onBattery && idleSeconds >= TRAINING_IDLE_SECONDS;
}

module.exports = {
  PREFERRED_FRONTEND_PORT,
  TRAINING_IDLE_SECONDS,
  backendCommand,
  backendEnv,
  trainerEnv,
  trainingAllowed,
  findFreePort,
  loadOrCreateSecrets,
  openLog,
  sqliteUrl,
  waitForHttp,
};
