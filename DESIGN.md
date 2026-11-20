---
name: Slearn
description: Fell und Gesicht – Graustufen und weiche Formen, abgeleitet vom Faultier-Maskottchen.
colors:
  ink: "#0a0a0a"
  paper: "#f2f2f1"
  face: "#ffffff"
  fur: "#cdcdcd"
  sidebar-paper: "#f2f2f1"
  secondary-fill: "#f4f4f4"
  muted-fill: "#f5f5f5"
  accent-fill: "#efefef"
  hairline: "#e7e7e7"
  input-line: "#dedede"
  muted-ink: "#6e6e6e"
typography:
  page-title:
    fontFamily: "Geist, ui-sans-serif, system-ui, sans-serif"
    fontSize: "22px"
    fontWeight: 600
    lineHeight: 1.25
  card-title:
    fontFamily: "Geist, ui-sans-serif, system-ui, sans-serif"
    fontSize: "14px"
    fontWeight: 600
    lineHeight: "20px"
    letterSpacing: "-0.01em"
  body:
    fontFamily: "Geist, ui-sans-serif, system-ui, sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: "20px"
  label:
    fontFamily: "Geist, ui-sans-serif, system-ui, sans-serif"
    fontSize: "11px"
    fontWeight: 500
    lineHeight: 1
rounded:
  xs: "3px"
  sm: "4px"
  md: "6px"
  lg: "8px"
  xl: "8px"
  2xl: "10px"
spacing:
  card: "16px"
  control-x: "14px"
components:
  button-primary:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.sidebar-paper}"
    rounded: "{rounded.md}"
    height: "36px"
    padding: "0 14px"
  button-secondary:
    backgroundColor: "{colors.secondary-fill}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    height: "36px"
  button-outline:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    height: "36px"
  card:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.lg}"
    padding: "16px"
  input:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    height: "36px"
    padding: "6px 12px"
  badge:
    backgroundColor: "{colors.secondary-fill}"
    textColor: "{colors.muted-ink}"
    rounded: "{rounded.sm}"
    height: "20px"
    padding: "0 6px"
---

# Design System: Slearn

## Overview

> **Stand 20.11.2026: „Fell und Gesicht“.** Die Formsprache folgt dem Faultier: grauer Seitengrund (Fell, `#f2f2f1`), weiße Karten mit 18 px Rundung und Haarlinie (Gesicht), Knöpfe/Badges/aktive Navigation ganz rund wie die Linienenden der Figur, Felder 10 px, Konturen von Bedienelementen in Fellgrau (`#cdcdcd`), Hover zu Tinte. Wo der Text unten „kleine Rundungen“, „nie Pillen“ oder „Karte = Haarlinie oben“ sagt, gilt dieser Absatz. Weiter gilt: keine Farbe, keine Schatten, keine Verläufe.

**Creative North Star: "Der ruhige Schreibtisch"**

Slearn sieht aus wie ein aufgeräumter Schreibtisch: weißes Papier, schwarze Tinte, feine Linien, nichts Lautes. Die Oberfläche tritt zurück, damit Stundenplan, Aufgaben und Noten das Bild bestimmen. Es gibt keine Akzentfarbe. Struktur entsteht durch Haarlinien und Flächen, die eine Graustufe heller oder dunkler als der Grund sind; Zustände unterscheiden sich über Gewicht, Füllung und deutsche Beschriftung.

Die Dichte ist die einer Arbeitsanwendung: Fließtext 13 px, Steuerelemente 36 px hoch, Karten mit 16 px Innenabstand. Ruhe heißt hier Präzision, nicht Leere. Motivationsfloskeln, Konfetti und Dekor gehören nicht auf diesen Schreibtisch; das Faultier ist die einzige Figur und lebt in denselben Graustufen.

**Key Characteristics:**
- Schwarz-Weiß mit Graustufenleiter, hell und dunkel gleichwertig
- Haarlinien statt Schatten, Flächen statt Verläufen
- Kleine Rundungen (6 px Standard), nie Pillen
- Geist als einzige Schrift, nur Gewichte 400, 500 und 600
- Zustand über Gewicht, Füllung und Text, nie über Farbe

## Colors

Eine neutrale Graustufenleiter ohne Akzent; Hell und Dunkel teilen dieselben Rollen und tauschen nur die Werte (Dunkelwerte im Sidecar).

### Primary
- **Tinte** (#0a0a0a): Text, primäre Schaltflächen, Fokusring, Auswahl. Im Dunkeln wird sie zu hellem Grau (#ededed).

### Neutral
- **Papier** (#ffffff): Seiten- und Kartengrund.
- **Seitenleistenpapier** (#fafafa): Seitenleiste, Text auf Tinte.
- **Sekundärfläche** (#f4f4f4) und **Gedämpfte Fläche** (#f5f5f5): Sekundärknöpfe, Hover, ruhige Flächen.
- **Akzentfläche** (#efefef): Hover auf Sekundärfläche, aktive Zeilen.
- **Haarlinie** (#e7e7e7): Kartenrand, Trenner.
- **Eingabelinie** (#dedede): Rand von Feldern.
- **Gedämpfte Tinte** (#6e6e6e): Nebentext, Beschreibungen, Platzhalter.

### Named Rules
**The No-Accent Rule.** Es gibt keine Farbe. Wer einen Zustand zeigen will, nimmt Gewicht (500 gegen 600), Füllung (Tinte gegen Papier) oder ein Wort, nie einen Farbton.
**The Destructive-Is-Ink Rule.** `destructive` ist Tinte, nicht Rot. Gefahr wird durch Wortlaut und Bestätigung getragen.

## Typography

**Font:** Geist (npm-Paket `geist`, von Next selbst ausgeliefert; Fallback ui-sans-serif, system-ui). Mono: Geist Mono für Code.

**Character:** Eine einzige, neutrale Grotesk. Hierarchie entsteht durch Größe und Gewicht, nicht durch Schriftwechsel.

### Hierarchy
- **Seitentitel** (600, 22px, 1.25): einmal je Seite.
- **Kartentitel** (600, 14px, 20px, leicht negativ gesetzt): `CardTitle`, ohne Icon.
- **Fließtext** (400, 13px, 20px): Standard in Karten und Listen; Seitenbasis ist 14px.
- **Label** (500, 11px): Badges und kleine Zustände.
- **Schaltfläche** (500, 13px; klein 12px, groß 14px).

### Named Rules
**The Two-Weights Rule.** Nur `font-medium` und `font-semibold`; kein `font-bold`, kein `extrabold`.
**The No-Shouting Rule.** Keine `uppercase tracking-[…]`-Überschriften und keine Motivationssätze in Karten.

## Layout

Seitenleiste links (`#fafafa`), Hauptspalte rechts, auf kleinen Breiten mit unterer Navigation und `env(safe-area-inset-bottom)`. Mindestbreite Desktop 960 px. Das Dashboard beginnt mit einer Kennzahlenleiste (nächste Stunde zuerst), darunter der Stundenplan, der mit „Heute“ startet. Karten tragen 16 px Innenabstand; Abstände folgen der Tailwind-Skala ohne eigene Zwischenwerte. Eigene Routen statt Hash-Ankern auf Unterseiten. In der Desktop-App lässt die `desktop:`-Variante Platz für die Fensterleiste.

## Elevation & Depth

Flach. Es gibt keine Schatten und keine Verläufe. Tiefe entsteht durch Haarlinien (#e7e7e7) und Flächen, die eine Stufe heller oder dunkler als der Grund sind. Ausnahme: ein schwacher Schimmer (`skeleton-shimmer`) für Ladezustände.

### Named Rules
**The Flat-Always Rule.** Keine `shadow-*`, kein `bg-gradient-*`. Wer Hierarchie braucht, nimmt eine Linie oder eine Grauschicht.

## Shapes

Kleine, ruhige Rundungen: 3 px (xs), 4 px (Badge), 6 px (Standard für Knöpfe und Felder), 8 px (Karten). `rounded-xl` und `rounded-2xl` sind bewusst auf 8 und 10 px gedeckelt, damit ältere Stellen nicht als Pillen auffallen. Ränder sind 1 px Haarlinien.

## Components

Ruhig und präzise: kleine Rundungen, feine Linien, Zustand über Gewicht und Füllung.

### Buttons
- **Shape:** 6 px Rundung, 36 px hoch (klein 32 px, groß 40 px, Icon 32 × 32).
- **Primary:** Tinte als Füllung, helle Schrift; Hover leicht aufgehellt.
- **Secondary:** Sekundärfläche ohne Rand; Hover Akzentfläche.
- **Outline / Ghost:** Haarlinie bzw. nur Text in gedämpfter Tinte; Hover gedämpfte Fläche.
- **Active / Disabled:** 80 % Deckkraft beim Drücken, 40 % wenn gesperrt.

### Cards / Containers
- **Corner Style:** 8 px, 1 px Haarlinie, Papier-Hintergrund.
- **Internal Padding:** 16 px; Titel `CardTitle`, Beschreibung in gedämpfter Tinte 13 px.

### Inputs / Fields
- **Style:** 36 px hoch, transparent, Eingabelinie, 6 px Rundung.
- **Focus:** Rand wird Tinte, dazu 2 px Fokusring mit 2 px Abstand.
- **Error:** Rand und 1 px Ring in Tinte, Text erklärt den Fehler.

### Badges
- 20 px hoch, 4 px Rundung, 11 px Text. Varianten unterscheiden sich über Füllung (voll, leer, grau) und Gewicht, nie über Farbe.

### Navigation
- Seitenleiste mit Gruppen „Bibliothek“ und „Werkzeuge“; Aktivzustand über den Pfad, nicht über Scroll-Verfolgung. Unten auf kleinen Breiten eine Leiste mit Safe-Area-Abstand.

### Faultier (Signaturfigur)
- Inline-SVG in Graustufen (`--sloth-*`), mit Zuständen ruhe, zuhören, denken, sprechen, freut, schläft. Der Kopf allein steht neben Antworten, die ganze Figur in der Seitenleiste von `/assistent` und in der Dashboard-Karte.

## Do's and Don'ts

### Do:
- **Do** Tokens aus `app/globals.css` benutzen (`bg-card`, `text-muted-foreground`, `border-border`); Farben stehen an vier Stellen hartkodiert und müssen gemeinsam geändert werden.
- **Do** Zustände über Gewicht, Füllung und Text zeigen.
- **Do** Fokus sichtbar halten (2 px Ring) und `prefers-reduced-motion` respektieren.
- **Do** neue Texte deutsch schreiben und durch `t()` schicken.
- **Do** Bewegung kurz halten (140 ms Standard, 100 ms schnell, 280 ms langsam).

### Don't:
- **Don't** Farbklassen, Verläufe oder Schatten einführen.
- **Don't** `font-bold`, `uppercase tracking-[…]`-Überschriften oder `Sparkles`-Icons benutzen.
- **Don't** Karten schachteln oder Titel mit Icon-Kacheln versehen.
- **Don't** Motivationssätze („Du machst den Unterschied“) in Karten schreiben.
- **Don't** externe Schriften, Skripte oder Bilder laden; die CSP erlaubt nur `'self'`, `data:` und `blob:`.
