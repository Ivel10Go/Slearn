import type { ReactNode } from "react";
import { AppPreview } from "@/components/app-preview";
import { AssistantPreview } from "@/components/assistant-preview";
import { Faultier } from "@/components/faultier";
import { WaitlistForm } from "@/components/waitlist-form";
import { cn } from "@/lib/utils";

const NAVIGATION = [
  { href: "#funktionen", label: "Funktionen" },
  { href: "#assistent", label: "Assistent" },
  { href: "#datenschutz", label: "Datenschutz" },
  { href: "#varianten", label: "Desktop & Server" },
  { href: "#fragen", label: "Fragen" },
];

const FUNKTIONEN: { titel: string; text: string; punkte: string[] }[] = [
  {
    titel: "Stundenplan",
    text: "Holt deinen Stundenplan aus WebUntis – einmal am Tag automatisch oder per Knopfdruck. Ausfälle sind sofort sichtbar.",
    punkte: ["Vier Wochen im Voraus", "Ausfälle und Raumwechsel", "Fächer mit Kürzel und Farbe"],
  },
  {
    titel: "Hausaufgaben, Prüfungen, Termine",
    text: "Alles mit Fälligkeit an einem Ort. Das Dashboard beantwortet nur eine Frage: Was steht heute an?",
    punkte: ["Kennzahlen auf einen Blick", "Erinnerungen per Push", "Offline lesbar"],
  },
  {
    titel: "Lernpläne",
    text: "Pläne, Aufgaben und wiederkehrende Serien. Aufgaben können aufeinander warten, und die Warteschlange bleibt sichtbar.",
    punkte: ["Prioritäten 0–5", "Abhängigkeiten ohne Zyklen", "Täglich, wöchentlich, monatlich"],
  },
  {
    titel: "Klausur-Generator",
    text: "Aus einer Prüfung und ihren Themen entsteht ein Lernplan. Das schwächste Thema kommt zuerst, weil es den meisten Vorlauf braucht.",
    punkte: ["Verteilt bis zum Klausurtag", "Richtet sich nach deinem Lernstand", "Ein zweiter Klick legt nichts doppelt an"],
  },
  {
    titel: "Lernfortschritt",
    text: "Fünf Stufen von „unsicher“ bis „gefestigt“, jede mit einem festen Wiederholungsabstand. Erklärbar in einem Satz, kein Black-Box-Algorithmus.",
    punkte: ["Wiederholung nach 1 bis 30 Tagen", "Lernserie und Wochenziel in Minuten", "Lernzeit je Fach und Wochentag"],
  },
  {
    titel: "Noten",
    text: "Einzelnoten mit Gewichtung, Schnitt je Fach und eine Hochrechnung: Was muss noch kommen, damit es reicht? Der Assistent sieht die Noten nur mit deiner Freigabe oder wenn alles lokal läuft.",
    punkte: ["Gewichteter Schnitt und Verlauf", "Hochrechnung bis zur Bestehensgrenze", "Import aus einer Notenliste"],
  },
  {
    titel: "Kalender",
    text: "Termine aus iCalendar, Google Kalender oder Apple Kalender – lesen und, wenn du willst, auch zurückschreiben.",
    punkte: ["Abo-Link für dein Handy", "Schreiben nur mit Freigabe", "Zugangsdaten verschlüsselt"],
  },
  {
    titel: "Unterrichtsmaterial",
    text: "Lade Aufschriebe, Arbeitsblätter und Lösungen hoch, auch als Foto. Lernraum liest sie, erkennt Fach, Thema und Datum und findet sie nach Bedeutung wieder.",
    punkte: ["Texterkennung für Fotos und Scans", "Suche nach Bedeutung, nicht nur Wörtern", "Korrekturen bleiben erhalten"],
  },
  {
    titel: "Belohnungen",
    text: "Punkte gibt es für gelernte Zeit an einem Fach, nicht fürs Abhaken. Gedeckelt pro Tag, damit es ein Anreiz bleibt und kein Wettrennen.",
    punkte: ["Gestaffelt statt linear", "Tagesgrenze", "Jede Buchung nachvollziehbar"],
  },
];

const LERNT = [
  {
    titel: "Notizen",
    text: "Sagst du „Merke: Ich lerne abends besser“, schreibt der Assistent es auf. Jede Notiz siehst du und kannst sie löschen.",
  },
  {
    titel: "Lernprofil",
    text: "Unsichere und fällige Themen, Lernzeiten, Abfrageergebnisse – nur aus deinen Daten gerechnet, nie geraten.",
  },
  {
    titel: "Persönliches Training",
    text: "Mit Freigabe und passender Grafikkarte lernt ein kleines Zusatzmodell deinen Stil. Es wird nur aktiviert, wenn es nachweislich besser ist.",
  },
];

const DATENSCHUTZ = [
  {
    titel: "Lokal zuerst",
    text: "Sprachmodell, Spracherkennung und Vorlesen laufen auf deinem Rechner. Ein Online-Dienst ist optional, und jede Antwort sagt, woher sie kommt.",
  },
  {
    titel: "Deine Daten bleiben bei dir",
    text: "Die Desktop-App speichert alles in deinem Benutzerordner. Kein Konto, keine Cloud, kein Tracking. Gespräche liegen nur im Browser.",
  },
  {
    titel: "Freigaben sind aus",
    text: "Noten teilen, Kalender beschreiben, aus Gesprächen lernen: alles ist standardmäßig aus und jederzeit widerrufbar.",
  },
  {
    titel: "Verschlüsselte Zugangsdaten",
    text: "Passwörter für WebUntis, Kalender und KI-Dienste werden mit AES-GCM verschlüsselt gespeichert und nur im Moment des Abrufs entschlüsselt.",
  },
];

const FRAGEN = [
  {
    frage: "Was kostet Lernraum?",
    antwort:
      "Die Preise geben wir zum Start bekannt – die Warteliste erfährt es zuerst. Einen Online-KI-Dienst brauchst du nicht; bindest du einen an, gelten dessen Preise.",
  },
  {
    frage: "Brauche ich eine Internetverbindung?",
    antwort:
      "Nur für den Stundenplan-Abruf, Kalender und das einmalige Herunterladen der Modelle. Danach funktioniert Lernraum inklusive Assistent offline.",
  },
  {
    frage: "Welcher Rechner reicht?",
    antwort:
      "Windows (x64) oder macOS (Apple Silicon und Intel). Wie viel Arbeitsspeicher ein lokales Modell braucht, zeigt die App vor dem Herunterladen an. Das persönliche Training braucht eine NVIDIA-Grafikkarte ab 6 GB oder einen Mac mit Apple Silicon ab 16 GB.",
  },
  {
    frage: "Muss meine Schule WebUntis nutzen?",
    antwort:
      "Nein. Du kannst den Stundenplan auch selbst eintragen oder als PDF oder Foto hochladen; Lernraum schlägt dann einen Plan vor, den du vor dem Übernehmen prüfst. WebUntis ist nur der bequemste Weg.",
  },
  {
    frage: "Was passiert mit meiner E-Mail-Adresse?",
    antwort:
      "Wir benutzen sie genau einmal: um dir zu schreiben, wenn Lernraum verfügbar ist. Sie wird nicht weitergegeben. Eine kurze Antwort auf die Mail genügt, und wir löschen sie.",
  },
  {
    frage: "Kann ich Lernraum selbst hosten?",
    antwort:
      "Ja. Die Server-Variante läuft mit Docker Compose auf einem eigenen Rechner oder Heimserver, mit PostgreSQL und Anmeldung für mehrere Personen.",
  },
];

export default function LandingPage() {
  return (
    <div className="min-h-screen">
      <Kopf />
      <main>
        <Hero />
        <Funktionen />
        <Assistent />
        <Datenschutz />
        <Varianten />
        <Warteliste />
        <Fragen />
      </main>
      <Fuss />
    </div>
  );
}

function Kopf() {
  return (
    <header className="sticky top-0 z-20 border-b border-border bg-background/90 backdrop-blur supports-[backdrop-filter]:bg-background/75">
      <div className="mx-auto flex h-14 max-w-6xl items-center justify-between gap-4 px-4 sm:px-6">
        <a href="#" className="flex items-center gap-2 text-[15px] font-semibold tracking-tight">
          <Faultier groesse={24} still />
          Lernraum
        </a>
        <nav aria-label="Seitenbereiche" className="hidden items-center gap-1 md:flex">
          {NAVIGATION.map((punkt) => (
            <a
              key={punkt.href}
              href={punkt.href}
              className="rounded-md px-2.5 py-1.5 text-[13px] text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              {punkt.label}
            </a>
          ))}
        </nav>
        <a
          href="#warteliste"
          className="inline-flex h-8 items-center rounded-md border border-primary bg-primary px-3 text-[13px] font-medium text-primary-foreground transition-colors hover:bg-primary/88"
        >
          Warteliste
        </a>
      </div>
    </header>
  );
}

function Hero() {
  return (
    <section className="border-b border-border">
      <div className="mx-auto grid max-w-6xl gap-12 px-4 pt-14 pb-16 sm:px-6 lg:grid-cols-[1fr_auto] lg:items-end lg:pt-20">
        <div className="max-w-2xl">
          <p className="inline-flex items-center gap-2 rounded-md border border-border px-2 py-1 text-[12px] text-muted-foreground">
            <span className="size-1.5 rounded-full bg-foreground" aria-hidden />
            Bald für Windows und macOS
          </p>
          <h1 className="mt-5 text-[40px] leading-[1.05] font-semibold tracking-[-0.03em] text-balance sm:text-[56px]">
            Der Schulalltag an einem Ort.
          </h1>
          <p className="mt-5 max-w-xl text-[16px] leading-relaxed text-muted-foreground text-pretty sm:text-[17px]">
            Stundenplan, Hausaufgaben, Prüfungen, Lernpläne und Noten – dazu ein Lern-Assistent, der deine Unterlagen
            kennt und auf deinem eigenen Rechner läuft.
          </p>
          <WaitlistForm kompakt className="mt-8 max-w-lg" />
        </div>
        <div className="hidden justify-self-end lg:block">
          <Faultier koerper groesse={150} folgen beschriftung="Das Lernraum-Faultier" />
        </div>
      </div>
      <div className="mx-auto max-w-6xl px-4 pb-16 sm:px-6">
        <AppPreview />
      </div>
    </section>
  );
}

function Abschnitt({ id, titel, text, children, className }: { id: string; titel: string; text: string; children: ReactNode; className?: string }) {
  return (
    <section id={id} className={cn("border-b border-border", className)}>
      <div className="mx-auto max-w-6xl px-4 py-16 sm:px-6 sm:py-20">
        <div className="max-w-2xl">
          <h2 className="text-[28px] leading-tight font-semibold tracking-[-0.02em] text-balance sm:text-[34px]">{titel}</h2>
          <p className="mt-3 text-[15px] leading-relaxed text-muted-foreground text-pretty">{text}</p>
        </div>
        <div className="mt-10">{children}</div>
      </div>
    </section>
  );
}

function Funktionen() {
  return (
    <Abschnitt
      id="funktionen"
      titel="Alles, was die Schule verlangt. Nichts, was ablenkt."
      text="Lernraum ersetzt das Hausaufgabenheft, den Lernplan auf Papier und die Notizen-App. Jede Funktion hat einen Grund und tut genau das."
    >
      <div className="grid gap-px overflow-hidden rounded-xl border border-border bg-border sm:grid-cols-2 lg:grid-cols-3">
        {FUNKTIONEN.map((funktion, index) => (
          <article key={funktion.titel} className="flex flex-col bg-background p-5 sm:p-6">
            <p className="font-mono text-[11px] text-muted-foreground tabular-nums">{String(index + 1).padStart(2, "0")}</p>
            <h3 className="mt-3 text-[16px] font-semibold tracking-tight">{funktion.titel}</h3>
            <p className="mt-2 text-[13.5px] leading-relaxed text-muted-foreground">{funktion.text}</p>
            <ul className="mt-4 space-y-1.5 border-t border-border pt-4">
              {funktion.punkte.map((punkt) => (
                <li key={punkt} className="flex gap-2 text-[13px]">
                  <span className="mt-[8px] h-px w-2.5 shrink-0 bg-foreground" aria-hidden />
                  {punkt}
                </li>
              ))}
            </ul>
          </article>
        ))}
      </div>
    </Abschnitt>
  );
}

function Assistent() {
  return (
    <Abschnitt
      id="assistent"
      titel="Ein Assistent, der deine Unterlagen kennt."
      text="Frag nach deinem Aufschrieb, lass dich abfragen oder diktiere die nächste Klausur. Er antwortet aus deinen Dokumenten und sagt ehrlich, wenn er nichts findet."
    >
      <div className="grid gap-10 lg:grid-cols-[1fr_1.1fr] lg:items-start">
        <div className="space-y-8">
          <ul className="space-y-5">
            <Punkt titel="Schlägt vor, statt still einzutragen">
              Sagst du „Bio-Klausur nächsten Donnerstag“, entsteht ein Vorschlag mit Datum. Eingetragen wird erst, wenn
              du auf „Eintragen“ tippst.
            </Punkt>
            <Punkt titel="Fragt dich ab">
              Aus deinem Material entstehen Fragen. Deine Antworten verschieben den Lernstand des Themas – so weiß der
              Lernplan, wo du stehst.
            </Punkt>
            <Punkt titel="Hört zu und spricht">
              Im Gesprächsmodus bleibt das Mikrofon offen, und die Antworten werden vorgelesen. Erkennung und Stimme
              laufen lokal mit Whisper und Piper.
            </Punkt>
            <Punkt titel="Zeigt seine Quellen">
              Jede Antwort nennt die Auszüge, auf die sie sich stützt, und ob sie lokal, von einem Online-Dienst oder
              ohne Modell entstanden ist.
            </Punkt>
          </ul>
          <div className="rounded-xl border border-border p-5">
            <p className="text-[13px] font-medium">Lernt an dir – auf drei Ebenen</p>
            <ol className="mt-4 grid gap-4 sm:grid-cols-3">
              {LERNT.map((ebene, index) => (
                <li key={ebene.titel}>
                  <p className="font-mono text-[11px] text-muted-foreground">Ebene {index + 1}</p>
                  <p className="mt-1 text-[13.5px] font-medium">{ebene.titel}</p>
                  <p className="mt-1 text-[12.5px] leading-relaxed text-muted-foreground">{ebene.text}</p>
                </li>
              ))}
            </ol>
          </div>
        </div>
        <AssistantPreview />
      </div>
    </Abschnitt>
  );
}

function Punkt({ titel, children }: { titel: string; children: ReactNode }) {
  return (
    <li className="border-l border-foreground pl-4">
      <p className="text-[15px] font-medium">{titel}</p>
      <p className="mt-1 text-[13.5px] leading-relaxed text-muted-foreground">{children}</p>
    </li>
  );
}

function Datenschutz() {
  return (
    <Abschnitt
      id="datenschutz"
      titel="Gebaut für Schülerdaten."
      text="Noten, Stundenplan und Aufschriebe sind persönlich. Deshalb ist der Normalfall in Lernraum: alles bleibt auf deinem Gerät."
      className="bg-muted/40"
    >
      <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
        {DATENSCHUTZ.map((punkt) => (
          <div key={punkt.titel} className="border-t border-foreground pt-4">
            <h3 className="text-[15px] font-semibold tracking-tight">{punkt.titel}</h3>
            <p className="mt-2 text-[13.5px] leading-relaxed text-muted-foreground">{punkt.text}</p>
          </div>
        ))}
      </div>
    </Abschnitt>
  );
}

const VERGLEICH: [string, string, string][] = [
  ["Für wen", "Eine Person an ihrem Rechner", "Familien, Lerngruppen, Selbsthoster"],
  ["Installation", "Installer starten, Namen eingeben", "Docker Compose"],
  ["Anmeldung", "Keine", "Benutzername und Passwort"],
  ["Daten", "SQLite im Benutzerordner", "PostgreSQL mit pgvector"],
  ["KI-Modelle", "Eingebaut, Download in der App", "Eigener Ollama-, LM-Studio- oder llama.cpp-Server"],
  ["Läuft auf", "Windows x64, macOS (Apple Silicon und Intel)", "Linux mit Docker (x64, ARM64 in Vorbereitung)"],
];

function Varianten() {
  return (
    <Abschnitt
      id="varianten"
      titel="Als App oder auf dem eigenen Server."
      text="Dieselbe Oberfläche, dieselben Funktionen. Die Desktop-App ist für eine Person gedacht, der Server für alle, die mehrere Konten selbst betreiben möchten."
    >
      <div className="overflow-x-auto rounded-xl border border-border">
        <table className="w-full min-w-[560px] border-collapse text-left text-[13.5px]">
          <thead>
            <tr className="border-b border-border">
              <th scope="col" className="w-1/4 px-4 py-3 font-normal text-muted-foreground" />
              <th scope="col" className="px-4 py-3 font-semibold">
                Desktop-App
              </th>
              <th scope="col" className="border-l border-border px-4 py-3 font-semibold">
                Server
              </th>
            </tr>
          </thead>
          <tbody>
            {VERGLEICH.map(([zeile, desktop, server]) => (
              <tr key={zeile} className="border-b border-border last:border-b-0">
                <th scope="row" className="px-4 py-3 font-normal text-muted-foreground">
                  {zeile}
                </th>
                <td className="px-4 py-3">{desktop}</td>
                <td className="border-l border-border px-4 py-3">{server}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Abschnitt>
  );
}

function Warteliste() {
  return (
    <section id="warteliste" className="border-b border-border">
      <div className="mx-auto grid max-w-6xl gap-10 px-4 py-16 sm:px-6 sm:py-20 lg:grid-cols-[1fr_440px] lg:gap-16">
        <div className="max-w-xl">
          <h2 className="text-[28px] leading-tight font-semibold tracking-[-0.02em] text-balance sm:text-[34px]">
            Sei dabei, wenn es losgeht.
          </h2>
          <p className="mt-3 text-[15px] leading-relaxed text-muted-foreground">
            Die ersten Installer verschicken wir in kleinen Runden an die Warteliste. Sag uns, wofür du Lernraum nutzen
            willst – das entscheidet, welche Plattform zuerst fertig wird.
          </p>
          <dl className="mt-8 grid grid-cols-3 gap-4 border-t border-border pt-6">
            <div>
              <dt className="text-[12px] text-muted-foreground">Plattformen</dt>
              <dd className="mt-1 text-[15px] font-medium">Windows, macOS</dd>
            </div>
            <div>
              <dt className="text-[12px] text-muted-foreground">Konto</dt>
              <dd className="mt-1 text-[15px] font-medium">Nicht nötig</dd>
            </div>
            <div>
              <dt className="text-[12px] text-muted-foreground">E-Mails</dt>
              <dd className="mt-1 text-[15px] font-medium">Eine zum Start</dd>
            </div>
          </dl>
          <div className="mt-10 hidden items-center gap-3 lg:flex">
            <Faultier zustand="zuhoeren" groesse={40} folgen />
            <p className="text-[13px] text-muted-foreground">Das Faultier wartet geduldig mit.</p>
          </div>
        </div>
        <div className="rounded-xl border border-border bg-card p-5 sm:p-6">
          <WaitlistForm />
        </div>
      </div>
    </section>
  );
}

function Fragen() {
  return (
    <Abschnitt id="fragen" titel="Häufige Fragen" text="Was du vor dem Eintragen wissen solltest.">
      <div className="divide-y divide-border rounded-xl border border-border">
        {FRAGEN.map((eintrag) => (
          <details key={eintrag.frage} className="group px-5">
            <summary className="flex cursor-pointer items-center justify-between gap-4 py-4 text-[14.5px] font-medium">
              {eintrag.frage}
              <svg viewBox="0 0 12 12" className="faq-plus size-3 shrink-0 transition-transform" aria-hidden>
                <path d="M6 1v10M1 6h10" stroke="currentColor" strokeWidth="1.4" />
              </svg>
            </summary>
            <p className="max-w-3xl pb-5 text-[13.5px] leading-relaxed text-muted-foreground">{eintrag.antwort}</p>
          </details>
        ))}
      </div>
    </Abschnitt>
  );
}

function Fuss() {
  return (
    <footer>
      <div className="mx-auto flex max-w-6xl flex-col gap-4 px-4 py-10 text-[12.5px] text-muted-foreground sm:flex-row sm:items-center sm:justify-between sm:px-6">
        <p className="flex items-center gap-2">
          <Faultier groesse={18} still />
          Lernraum · Der Schulalltag an einem Ort.
        </p>
        <nav aria-label="Fußzeile" className="flex flex-wrap gap-4">
          <a href="#funktionen" className="hover:text-foreground">
            Funktionen
          </a>
          <a href="#datenschutz" className="hover:text-foreground">
            Datenschutz
          </a>
          <a href="#warteliste" className="hover:text-foreground">
            Warteliste
          </a>
        </nav>
      </div>
    </footer>
  );
}
