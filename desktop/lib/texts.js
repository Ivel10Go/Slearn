// @ts-check
"use strict";

/**
 * Texte der Desktop-Huelle (Menue, Infobereich, Dialoge) auf Deutsch und Englisch.
 *
 * Die Sprache waehlt die Seite (`frontend/lib/i18n.ts`) und meldet sie ueber
 * `lernraum:set-language`; bis dahin - und bei einem Start ohne Fenster - gilt
 * die Sprache des Systems. Gespeichert wird sie hier nicht: die Seite schickt
 * sie bei jedem Laden neu, und eine zweite Ablage koennte ihr widersprechen.
 */

/** @typedef {"de" | "en"} Language */

const TEXTS = {
  de: {
    serviceStopped: "Der Lernraum-Dienst wurde unerwartet beendet.",
    frontendStopped: "Die Oberfläche wurde unerwartet beendet.",
    startFailed: "Der Lernraum konnte nicht gestartet werden.",
    dataSafe: "Deine Daten sind nicht betroffen. Details stehen in den Protokollen:",
    quit: "Beenden",
    openLogs: "Protokolle öffnen",
    stillRunningTitle: "Lernraum läuft weiter",
    stillRunningBody: "Erinnerungen kommen weiterhin. Beenden kannst du Lernraum über das Symbol im Infobereich.",
    about: "Über Lernraum",
    hide: "Lernraum ausblenden",
    hideOthers: "Andere ausblenden",
    unhide: "Alle einblenden",
    quitApp: "Lernraum beenden",
    edit: "Bearbeiten",
    undo: "Widerrufen",
    redo: "Wiederholen",
    cut: "Ausschneiden",
    copy: "Kopieren",
    paste: "Einfügen",
    selectAll: "Alles auswählen",
    view: "Ansicht",
    reload: "Neu laden",
    resetZoom: "Originalgröße",
    zoomIn: "Vergrößern",
    zoomOut: "Verkleinern",
    fullscreen: "Vollbild",
    devTools: "Entwicklerwerkzeuge",
    window: "Fenster",
    minimize: "Minimieren",
    zoom: "Zoomen",
    close: "Schließen",
    help: "Hilfe",
    openDataFolder: "Datenordner öffnen",
    openApp: "Lernraum öffnen",
    openAtLogin: "Beim Anmelden starten",
    checkUpdates: "Nach Updates suchen",
    updatesUnsigned: "Updates lassen sich nur in einer signierten App installieren. Lade die neue Version von der Downloadseite.",
    updatesOffline: "Keine Verbindung zum Update-Server.",
    updatesFailed: "Die Suche nach Updates ist fehlgeschlagen.",
    updateReadyTitle: "Update bereit",
    updateReady: "Lernraum {version} ist bereit.",
    updateReadyDetail: "Deine Daten bleiben erhalten. Jetzt neu starten oder das Update beim nächsten Beenden installieren?",
    restartNow: "Jetzt neu starten",
    later: "Später",
    updatesTitle: "Updates",
    updatesInstalledOnly: "Updates gibt es nur in der installierten App.",
    upToDateVersion: "Lernraum {version} ist aktuell.",
  },
  en: {
    serviceStopped: "The Lernraum service stopped unexpectedly.",
    frontendStopped: "The interface stopped unexpectedly.",
    startFailed: "Lernraum could not be started.",
    dataSafe: "Your data is not affected. Details are in the logs:",
    quit: "Quit",
    openLogs: "Open logs",
    stillRunningTitle: "Lernraum keeps running",
    stillRunningBody: "Reminders keep coming. You can quit Lernraum from the icon in the notification area.",
    about: "About Lernraum",
    hide: "Hide Lernraum",
    hideOthers: "Hide others",
    unhide: "Show all",
    quitApp: "Quit Lernraum",
    edit: "Edit",
    undo: "Undo",
    redo: "Redo",
    cut: "Cut",
    copy: "Copy",
    paste: "Paste",
    selectAll: "Select all",
    view: "View",
    reload: "Reload",
    resetZoom: "Actual size",
    zoomIn: "Zoom in",
    zoomOut: "Zoom out",
    fullscreen: "Full screen",
    devTools: "Developer tools",
    window: "Window",
    minimize: "Minimise",
    zoom: "Zoom",
    close: "Close",
    help: "Help",
    openDataFolder: "Open data folder",
    openApp: "Open Lernraum",
    openAtLogin: "Start at login",
    checkUpdates: "Check for updates",
    updatesUnsigned: "Updates can only be installed in a signed app. Download the new version from the download page.",
    updatesOffline: "No connection to the update server.",
    updatesFailed: "Checking for updates failed.",
    updateReadyTitle: "Update ready",
    updateReady: "Lernraum {version} is ready.",
    updateReadyDetail: "Your data is kept. Restart now or install the update the next time you quit?",
    restartNow: "Restart now",
    later: "Later",
    updatesTitle: "Updates",
    updatesInstalledOnly: "Updates are only available in the installed app.",
    upToDateVersion: "Lernraum {version} is up to date.",
  },
};

/** @typedef {keyof typeof TEXTS.de} TextKey */

/** @type {Language} */
let current = "de";

/** @param {unknown} value @returns {value is Language} */
function isLanguage(value) {
  return value === "de" || value === "en";
}

/** Die Systemsprache als Startwert: Deutsch fuer `de-*`, sonst Englisch. @param {string} locale @returns {Language} */
function fromSystemLocale(locale) {
  return /^de\b/i.test(locale) ? "de" : "en";
}

/** @param {Language} language @returns {boolean} ob sich etwas geaendert hat */
function setLanguage(language) {
  if (current === language) {
    return false;
  }
  current = language;
  return true;
}

function getLanguage() {
  return current;
}

/**
 * @param {TextKey} key
 * @param {Record<string, string>} [values]
 */
function text(key, values = {}) {
  return TEXTS[current][key].replace(/\{(\w+)\}/g, (match, name) => (name in values ? values[name] : match));
}

module.exports = { TEXTS, fromSystemLocale, getLanguage, isLanguage, setLanguage, text };
