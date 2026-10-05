"use client";

import { useEffect, useRef, type RefObject } from "react";
import type { FaultierZustand } from "@/lib/faultier";
import { cn } from "@/lib/utils";

/*
 * Das Faultier ist das Gesicht des Assistenten.
 *
 * - **Zwei Formen:** `koerper` haengt die ganze Figur an einen Ast (leerer
 *   Chat, Seitenleiste, Dashboard), ohne ist es nur der Kopf (Avatar neben
 *   Antworten und im Gespraechsbalken).
 * - **Graustufen statt Farbe**, passend zum Schwarz-Weiss-Design. Die Toene
 *   stehen als `--sloth-*` in `globals.css`, mit eigener Dunkel-Variante.
 * - **Ruhig.** Langsames Schwingen und seltenes Blinzeln - es ist ein Faultier.
 *   `still` schaltet die Bewegung ab (aeltere Nachrichten), und
 *   `prefers-reduced-motion` greift ueber die globale Regel.
 * - **Augen folgen dem Zeiger** nur mit `folgen`, und dann direkt am DOM, ohne
 *   React-Zustand: ein Rendern pro Mausbewegung waere sinnlos.
 */

interface FaultierProps {
  zustand?: FaultierZustand;
  /** Breite in Pixeln. */
  groesse?: number;
  koerper?: boolean;
  folgen?: boolean;
  still?: boolean;
  /** Ohne Beschriftung ist die Figur Dekoration (`aria-hidden`). */
  beschriftung?: string;
  className?: string;
}

const KOPF_BOX = "26 25 68 64";
const KOERPER_BOX = "0 0 120 136";

export function Faultier({ zustand = "ruhe", groesse = 32, koerper = false, folgen = false, still = false, beschriftung, className }: FaultierProps) {
  const blick = useRef<SVGGElement | null>(null);
  const svg = useRef<SVGSVGElement | null>(null);
  const folgt = folgen && (zustand === "ruhe" || zustand === "zuhoeren" || zustand === "sprechen");

  useEffect(() => {
    const ziel = blick.current;
    if (!folgt || !ziel || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      return;
    }
    function bewegen(event: PointerEvent) {
      const rahmen = svg.current?.getBoundingClientRect();
      if (!rahmen || !ziel) {
        return;
      }
      const dx = event.clientX - (rahmen.left + rahmen.width / 2);
      const dy = event.clientY - (rahmen.top + rahmen.height * (koerper ? 0.43 : 0.5));
      const abstand = Math.hypot(dx, dy) || 1;
      const weite = Math.min(1, abstand / 240) * 1.3;
      ziel.style.transform = `translate(${((dx / abstand) * weite).toFixed(2)}px, ${((dy / abstand) * weite).toFixed(2)}px)`;
    }
    window.addEventListener("pointermove", bewegen, { passive: true });
    return () => {
      window.removeEventListener("pointermove", bewegen);
      ziel.style.transform = "";
    };
  }, [folgt, koerper]);

  const hoehe = koerper ? Math.round((groesse * 136) / 120) : groesse;

  return (
    <svg
      ref={svg}
      className={cn("faultier shrink-0", still && "faultier-still", className)}
      width={groesse}
      height={hoehe}
      viewBox={koerper ? KOERPER_BOX : KOPF_BOX}
      data-zustand={zustand}
      role={beschriftung ? "img" : undefined}
      aria-label={beschriftung}
      aria-hidden={beschriftung ? undefined : true}
      focusable="false"
    >
      {koerper ? (
        <>
          <path className="f-ast" d="M4 10 H116" />
          <path className="f-blatt" d="M100 10 c3 -7 10 -8 13 -6 c-2 6 -8 8 -13 6z" />
          <path className="f-blatt" d="M14 10 c-2 6 -8 8 -11 6 c1 -5 6 -8 11 -6z" />
          <g className="f-schwingen">
            <path className="f-arm-rand" d="M47 96 C31 86 25 52 33 13" />
            <path className="f-arm-rand" d="M73 96 C89 86 95 52 87 13" />
            <path className="f-arm" d="M47 96 C31 86 25 52 33 13" />
            <path className="f-arm" d="M73 96 C89 86 95 52 87 13" />
            <Krallen x={33} />
            <Krallen x={87} />
            <ellipse className="f-fell" cx="45" cy="131" rx="8" ry="5.5" />
            <ellipse className="f-fell" cx="75" cy="131" rx="8" ry="5.5" />
            <path className="f-fell" d="M60 76 C83 76 91 95 89 111 C87 127 76 133 60 133 C44 133 33 127 31 111 C29 95 37 76 60 76Z" />
            <ellipse className="f-bauch" cx="60" cy="111" rx="16" ry="16" />
            <Kopf zustand={zustand} blick={blick} />
            {zustand === "denken" ? (
              <g className="f-gedanken">
                <circle className="f-deko" cx="96" cy="40" r="2" />
                <circle className="f-deko" cx="102" cy="33" r="2.6" />
                <circle className="f-deko" cx="109" cy="25" r="3.2" />
              </g>
            ) : null}
            {zustand === "schlaeft" ? (
              <g className="f-zzz">
                <text className="f-deko" x="92" y="38" fontSize="9" fontWeight="600">
                  z
                </text>
                <text className="f-deko" x="100" y="29" fontSize="11" fontWeight="600">
                  z
                </text>
              </g>
            ) : null}
          </g>
        </>
      ) : (
        <g className="f-wippen">
          <Kopf zustand={zustand} blick={blick} />
        </g>
      )}
    </svg>
  );
}

function Krallen({ x }: { x: number }) {
  return (
    <>
      {[-4, 0, 4].map((versatz) => (
        <path key={versatz} className="f-kralle" d={`M${x + versatz - 2} 15 c-1 -5 3 -8 5.5 -4.5`} />
      ))}
    </>
  );
}

type BlickRef = RefObject<SVGGElement | null>;

function Kopf({ zustand, blick }: { zustand: FaultierZustand; blick: BlickRef }) {
  return (
    <g className="f-kopf">
      <ellipse className="f-fell" cx="60" cy="58" rx="31" ry="27" />
      <path className="f-linie f-duenn" d="M53 35 q2 -2.5 4 0 M59 34 q2 -2.5 4 0 M65 35 q2 -2.5 4 0" />
      <path className="f-gesicht" d="M60 43 C76 43 85 52 85 63 C85 74 74 81 60 81 C46 81 35 74 35 63 C35 52 44 43 60 43Z" />
      <ellipse className="f-wange" cx="41" cy="71" rx="4" ry="2.5" />
      <ellipse className="f-wange" cx="79" cy="71" rx="4" ry="2.5" />
      <path className="f-tinte" d="M56 56.5 C52 51.5 42 54.5 37 63 C35 67.5 39 70 43.5 68 C50.5 65 58 61.5 56 56.5Z" />
      <path className="f-tinte" d="M64 56.5 C68 51.5 78 54.5 83 63 C85 67.5 81 70 76.5 68 C69.5 65 62 61.5 64 56.5Z" />
      <Augen zustand={zustand} blick={blick} />
      <ellipse className="f-tinte" cx="60" cy="66" rx="4.4" ry="2.9" />
      <Mund zustand={zustand} />
    </g>
  );
}

function Augen({ zustand, blick }: { zustand: FaultierZustand; blick: BlickRef }) {
  if (zustand === "freut") {
    return <path className="f-lid" d="M45.5 61.5 q3 -3.5 6 0 M68.5 61.5 q3 -3.5 6 0" />;
  }
  if (zustand === "schlaeft") {
    return <path className="f-lid" d="M45.5 60 q3 3 6 0 M68.5 60 q3 3 6 0" />;
  }
  const gross = zustand === "zuhoeren";
  const radius = gross ? 3.6 : 3.1;
  const pupille = gross ? 2.2 : 1.9;
  return (
    <g className="f-augen">
      <circle className="f-augapfel" cx="48.5" cy="60.5" r={radius} />
      <circle className="f-augapfel" cx="71.5" cy="60.5" r={radius} />
      <g ref={blick} className={cn("f-blick", zustand === "denken" && "f-blick-oben")}>
        <circle className="f-tinte" cx="48.8" cy="60.8" r={pupille} />
        <circle className="f-tinte" cx="71.8" cy="60.8" r={pupille} />
      </g>
    </g>
  );
}

function Mund({ zustand }: { zustand: FaultierZustand }) {
  switch (zustand) {
    case "zuhoeren":
      return <ellipse className="f-tinte" cx="60" cy="73" rx="2" ry="2.4" />;
    case "denken":
      return <path className="f-linie" d="M55 73 q2.5 -2 5 0 t5 0" />;
    case "sprechen":
      return <ellipse className="f-tinte f-mund-spricht" cx="60" cy="73" rx="3.6" ry="3" />;
    case "freut":
      return <path className="f-tinte f-linie-rand" d="M53 70 Q60 79 67 70 Z" />;
    case "schlaeft":
      return <path className="f-linie" d="M57 73 q3 1.5 6 0" />;
    default:
      return <path className="f-linie" d="M60 68.5 v1.8 M54.5 70.5 q2.75 3 5.5 0 q2.75 3 5.5 0" />;
  }
}
