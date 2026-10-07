"use client";

import type { ReactNode } from "react";
import Link from "next/link";
import { AppShell } from "@/components/app-shell";
import { useSession } from "@/lib/use-session";

/**
 * Datenschutzerklaerung.
 *
 * Sie beschreibt, was der Code tatsaechlich tut - jede ausgehende Verbindung
 * hier ist eine, die der Code aufbaut, und jede steht unter einer Freigabe
 * oder einer Handlung der Nutzerin. Wer eine neue Verbindung einbaut, ergaenzt
 * sie hier, sonst stimmt die Erklaerung nicht mehr.
 *
 * Die Kontaktangabe des Betreibers kommt aus `NEXT_PUBLIC_DATENSCHUTZ_KONTAKT`
 * (beim Bauen eingesetzt). Ohne sie steht ein Hinweis statt einer erfundenen
 * Adresse.
 */
const KONTAKT = process.env.NEXT_PUBLIC_DATENSCHUTZ_KONTAKT?.trim() ?? "";
const STAND = "7. Oktober 2026";

function Abschnitt({ titel, children }: { titel: string; children: ReactNode }) {
  return (
    <section className="space-y-2">
      <h2 className="text-[15px] font-semibold">{titel}</h2>
      <div className="space-y-2 text-[13px] leading-6 text-muted-foreground">{children}</div>
    </section>
  );
}

export default function DatenschutzPage() {
  const user = useSession();
  return (
    <AppShell user={user}>
      <article className="max-w-3xl space-y-6">
        <header>
          <h1 className="text-[22px] font-semibold leading-8 tracking-tight">Datenschutz</h1>
          <p className="mt-0.5 text-[13px] leading-5 text-muted-foreground">Stand: {STAND}</p>
        </header>

        <Abschnitt titel="Kurz gesagt">
          <p>
            Der Lernraum speichert deine Daten dort, wo er läuft: in der Desktop-App auf deinem Rechner, im Server-Betrieb auf
            dem Server deiner Schule oder Familie. Es gibt kein Konto bei uns, keine Werbung, kein Tracking und keine
            Analyse-Dienste. Daten verlassen den Rechner nur, wenn du eine Verbindung einrichtest oder einen Download startest.
          </p>
        </Abschnitt>

        <Abschnitt titel="Verantwortlich">
          <p>
            Verantwortlich im Sinne der DSGVO ist, wer den Lernraum betreibt. Bei der Desktop-App bist das du selbst - die
            Herausgeber der App erhalten keine deiner Daten. Im Server-Betrieb ist es die Stelle, die den Server betreibt.
          </p>
          <p>{KONTAKT ? `Kontakt: ${KONTAKT}` : "Der Betreiber hat keine Kontaktadresse hinterlegt."}</p>
        </Abschnitt>

        <Abschnitt titel="Was gespeichert wird">
          <p>
            Was du einträgst oder importierst: Name und Zeitzone, Stundenplan, Fächer, Hausaufgaben, Klausuren, Noten,
            Termine, Lernpläne und Lernzeiten, hochgeladene Dokumente, Notizen des Assistenten, Punkte und Einstellungen.
            Gespräche mit dem Assistenten bleiben nur im Browser bzw. in der App auf diesem Gerät.
          </p>
          <p>
            Zugangsdaten zu Verbindungen (WebUntis, KI-Dienst, Kalender) werden verschlüsselt gespeichert und nur für die
            jeweilige Verbindung entschlüsselt.
          </p>
          <p>
            Trainingsbeispiele für ein persönliches Modell entstehen nur, wenn du das unter „KI &amp; Modelle“ ausdrücklich
            einschaltest (Standard: aus). Das Training läuft auf deinem eigenen Rechner.
          </p>
        </Abschnitt>

        <Abschnitt titel="Wann Daten den Rechner verlassen">
          <ul className="list-disc space-y-1 pl-5">
            <li>
              <strong className="font-medium text-foreground">WebUntis</strong> - nur mit eingerichteter Verbindung: Anmeldung
              und Abruf des Stundenplans beim Schulportal.
            </li>
            <li>
              <strong className="font-medium text-foreground">Online-KI-Dienst</strong> - nur mit eingerichteter Verbindung:
              deine Frage, passende Auszüge aus deinen Dokumenten und Termine gehen an den von dir gewählten Anbieter. Noten nur
              mit eigener Freigabe (Standard: aus). Lokale Modelle laufen ohne Internet.
            </li>
            <li>
              <strong className="font-medium text-foreground">Kalender</strong> (Google, Apple/iCloud, iCalendar) - nur mit
              eingerichteter Verbindung. Schreiben in einen fremden Kalender erst nach eigener Zustimmung.
            </li>
            <li>
              <strong className="font-medium text-foreground">Downloads</strong> - Modelle und Stimmen von Hugging Face,
              Laufzeiten und App-Updates von GitHub. Dabei werden keine Lerndaten übertragen, nur die üblichen Angaben jeder
              Web-Anfrage (IP-Adresse, Zeitpunkt).
            </li>
            <li>
              <strong className="font-medium text-foreground">Push-Benachrichtigungen</strong> - nur wenn du sie einschaltest:
              Der Browser-Hersteller stellt sie zu. Die Nachricht selbst ist Ende-zu-Ende verschlüsselt.
            </li>
          </ul>
        </Abschnitt>

        <Abschnitt titel="Rechtsgrundlage">
          <p>
            Die Verarbeitung dient der Nutzung der App, die du selbst eingerichtet hast (Art. 6 Abs. 1 lit. b DSGVO), bzw. deiner
            Einwilligung für die einzelnen Verbindungen und Freigaben (Art. 6 Abs. 1 lit. a DSGVO), die du jederzeit in den
            Einstellungen widerrufen kannst. Unter 16 Jahren brauchen Verbindungen zu Online-Diensten die Zustimmung der Eltern
            (Art. 8 DSGVO).
          </p>
        </Abschnitt>

        <Abschnitt titel="Deine Rechte">
          <p>
            Unter{" "}
            <Link href="/settings?tab=allgemein" className="underline underline-offset-2 hover:text-foreground">
              Einstellungen → Konto &amp; Darstellung → Deine Daten
            </Link>{" "}
            lädst du alle gespeicherten Daten als Datei herunter (Auskunft und Datenübertragbarkeit, Art. 15 und 20) oder
            löschst dein Konto mit allen Einträgen, Dokumenten, Trainingsdaten und Adaptern (Art. 17). Einzelne Einträge lassen
            sich jederzeit dort ändern oder löschen, wo sie stehen (Art. 16). Du hast außerdem das Recht, dich bei einer
            Datenschutz-Aufsichtsbehörde zu beschweren.
          </p>
        </Abschnitt>

        <Abschnitt titel="Speicherdauer">
          <p>
            Daten bleiben gespeichert, bis du sie oder dein Konto löschst. Beim Deinstallieren der Desktop-App bleibt der
            Datenordner erhalten, damit eine Neuinstallation nichts verliert; ihn findest du unter Einstellungen → „Daten auf
            diesem Rechner“.
          </p>
        </Abschnitt>
      </article>
    </AppShell>
  );
}
