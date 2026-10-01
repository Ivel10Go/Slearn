// @ts-check
"use strict";

/**
 * Die schmale Bruecke zwischen Seite und Desktop-App.
 *
 * Die Seite bekommt genau drei Dinge (Typ in `frontend/lib/desktop.ts`): die
 * Plattform, das Design an den Fensterrahmen melden und den Datenordner
 * oeffnen. Kein `ipcRenderer`, kein Node - die Seite ist dieselbe wie im
 * Browser und soll hier nicht mehr koennen als dort.
 */

const { contextBridge, ipcRenderer } = require("electron");

/** @param {string} name */
function argument(name) {
  const prefix = `--${name}=`;
  const found = process.argv.find((value) => value.startsWith(prefix));
  return found ? found.slice(prefix.length) : "";
}

contextBridge.exposeInMainWorld("lernraumDesktop", {
  platform: process.platform,
  version: argument("lernraum-version"),
  dataFolder: argument("lernraum-data"),
  /** @param {"light" | "dark" | "system"} theme */
  setTheme: (theme) => ipcRenderer.send("lernraum:set-theme", theme),
  openDataFolder: () => ipcRenderer.invoke("lernraum:open-data-folder"),
});

// Plattform-Attribute **vor** dem ersten Rendern setzen. Kaeme das erst aus
// React, sprange die Seitenleiste auf dem Mac nach dem Laden um die
// Ampelknoepfe nach unten.
function markDocument() {
  const root = document.documentElement;
  if (root) {
    root.dataset.desktop = "";
    root.dataset.platform = process.platform;
  }
}
markDocument();
document.addEventListener("DOMContentLoaded", markDocument);
