"use client";

import { useEffect, useRef, useState } from "react";

/**
 * Die "Leselinie": so weit unten im Viewport gilt ein Abschnitt als sichtbar.
 * Muss zum sticky Header passen (4rem) plus etwas Luft.
 */
const SCROLL_OFFSET = 96;

/**
 * Nach a Millisekunden, in denen wir der URL nicht mehr glauben.
 *
 * `scroll-behavior: smooth` laeuft nach einem Klick auf einen Anker noch
 * mehrere hundert Millisekunden. Ohne diese Sperre wuerde die Seitenleiste
 * waehrend der Animation durch alle Abschnitte wandern.
 */
const HASH_LOCK_MS = 700;

/**
 * Der Abschnitt an der Leselinie - unabhaengig davon, ob in der URL ein Hash
 * steht.
 *
 * Ohne das bleibt die Navigation stehen, sobald man selbst scrollt: die Seite
 * zeigt "Nutzung", die Sidebar weiter "Uebersicht".
 *
 * Bewusst *nicht* "welcher Abschnitt ist schon vorbei" - das Dashboard ist ein
 * zweispaltiges Grid, beide Spalten laufen nebeneinander, und die Reihenfolge
 * im DOM ist nicht die Reihenfolge im Blickfeld. "Welcher Abschnitt ueberdeckt
 * die Leselinie, und welcher davon endet als erstes" ist dagegen stabil: der
 * Wert wechselt erst, wenn ein neuer Abschnitt die Linie kreuzt, nicht bei
 * jedem Pixel.
 *
 * Der Hash wird absichtlich nicht in die URL geschrieben - das wuerde bei
 * jedem Scrollen einen Verlaufseintrag erzeugen.
 */
export function useActiveSection(sectionIds: readonly string[], enabled: boolean) {
  const [activeId, setActiveId] = useState<string | null>(null);
  const lockedUntil = useRef(0);

  useEffect(() => {
    if (!enabled || sectionIds.length === 0) {
      const frame = window.requestAnimationFrame(() => setActiveId(null));
      return () => window.cancelAnimationFrame(frame);
    }

    const measure = () => {
      const scroller = document.documentElement;
      // Nur wenn wirklich Platz zum Scrollen ist. Sonst wuerde eine kurze Seite
      // als "am Ende" gelten und den letzten Abschnitt statt der Übersicht
      // markieren.
      const scrollable = scroller.scrollHeight > window.innerHeight + 8;
      const atBottom = scrollable && window.innerHeight + window.scrollY >= scroller.scrollHeight - 2;

      const boxes: Array<{ id: string; top: number; bottom: number }> = [];
      for (const id of sectionIds) {
        const rect = document.getElementById(id)?.getBoundingClientRect();
        if (rect && rect.height > 0) {
          boxes.push({ id, top: rect.top, bottom: rect.bottom });
        }
      }
      if (boxes.length === 0) {
        return null;
      }

      if (atBottom) {
        return boxes.reduce((last, box) => (box.bottom > last.bottom ? box : last)).id;
      }

      const crossing = boxes
        .filter((box) => box.top - SCROLL_OFFSET <= 0 && box.bottom > SCROLL_OFFSET)
        .sort((a, b) => a.bottom - b.bottom);
      if (crossing.length > 0) {
        return crossing[0].id;
      }

      // Luecke zwischen zwei Karten: dann gilt der letzte bereits passierte.
      // Nach der unteren Kante sortiert, nicht nach der Reihenfolge im Array -
      // das Dashboard ist ein Grid, die Reihenfolge im DOM ist damit nicht mehr
      // die Reihenfolge im Blickfeld.
      const above = boxes.filter((box) => box.bottom <= SCROLL_OFFSET);
      if (above.length === 0) {
        return boxes[0].id;
      }
      return above.reduce((last, box) => (box.bottom > last.bottom ? box : last)).id;
    };

    const update = () => {
      if (performance.now() < lockedUntil.current) {
        return;
      }
      const next = measure();
      setActiveId((current) => (current === next ? current : next));
    };

    const lock = () => {
      lockedUntil.current = performance.now() + HASH_LOCK_MS;
    };

    let frame = 0;
    const schedule = () => {
      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(update);
    };

    update();
    window.addEventListener("hashchange", lock);
    window.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("hashchange", lock);
      window.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", schedule);
    };
  }, [sectionIds, enabled]);

  return activeId;
}
