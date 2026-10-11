/*
 * Alle Texte der Landingpage, je Sprache ein ganzes Objekt.
 *
 * Bewusst kein Schluessel-Woerterbuch wie `frontend/lib/i18n.ts`: die Seite ist
 * fast nur Fliesstext, und ein ganzer Satz je Sprache liest sich besser als
 * hundert Einzelschluessel. `LandingContent` erzwingt, dass beide Sprachen
 * dieselben Felder haben; `content.test.ts` prueft zusaetzlich gleich lange
 * Listen und passende Sprungmarken. Deutsch liegt unter `/`, Englisch unter `/en/`.
 *
 * Aussagen nur aus dem Code (siehe AGENTS.md, „Landingpage“) – das gilt fuer
 * die Uebersetzung genauso.
 */

import type { Platform, Role } from "./waitlist";

export type Locale = "de" | "en";

export const LOCALES: Locale[] = ["de", "en"];

export type SectionId = "features" | "assistant" | "privacy" | "editions" | "waitlist" | "faq";

export interface FormContent {
  emailLabel: string;
  emailPlaceholder: string;
  nameLabel: string;
  optional: string;
  roleLabel: string;
  choose: string;
  roles: Record<Role, string>;
  platformLegend: string;
  platforms: Record<Platform, string>;
  consent: string;
  submit: string;
  sending: string;
  doneTitle: string;
  doneMessage: string;
  honeypotLabel: string;
  errors: {
    emailMissing: string;
    emailInvalid: string;
    name: string;
    role: string;
    platform: string;
    consent: string;
    unreadable: string;
    forbidden: string;
    tooLarge: string;
    rateLimited: string;
    unavailable: string;
    generic: string;
    offline: string;
  };
}

export interface LandingContent {
  locale: Locale;
  meta: { title: string; description: string; ogLocale: string };
  /** Sprungmarken je Sprache; die deutschen bleiben, wie sie schon verlinkt sind. */
  ids: Record<SectionId, string>;
  header: {
    homeLabel: string;
    navLabel: string;
    nav: { section: SectionId; label: string }[];
    waitlist: string;
    /** Beschriftung des Sprachumschalters, zeigt auf die *andere* Sprache. */
    otherLanguage: { short: string; label: string };
  };
  /** Hinweis in *dieser* Sprache fuer Besucher der anderen Fassung, deren Browser diese Sprache bevorzugt. */
  languageHint: { text: string; action: string; dismiss: string };
  hero: { badge: string; title: string; lead: string; mascot: string };
  features: { title: string; text: string; items: { title: string; text: string; points: string[] }[] };
  assistant: {
    title: string;
    text: string;
    points: { title: string; text: string }[];
    learnsTitle: string;
    /** `{n}` wird durch die Nummer ersetzt. */
    level: string;
    levels: { title: string; text: string }[];
  };
  privacy: { title: string; text: string; items: { title: string; text: string }[] };
  editions: { title: string; text: string; desktop: string; server: string; rows: [string, string, string][] };
  waitlist: { title: string; text: string; facts: { label: string; value: string }[]; mascotNote: string };
  faq: { title: string; text: string; items: { question: string; answer: string }[] };
  footer: { tagline: string; navLabel: string; links: { section: SectionId; label: string }[] };
  appPreview: {
    label: string;
    nav: { group: string; items: string[] }[];
    active: string;
    greeting: string;
    summary: string;
    source: string;
    stats: { title: string; value: string; note: string }[];
    today: string;
    lessons: { time: string; short: string; name: string; room: string; now?: boolean; cancelled?: boolean }[];
    cancelled: string;
    homework: string;
    tasks: { title: string; subject: string; due: string; done: boolean }[];
  };
  assistantPreview: {
    label: string;
    question: string;
    answer: string;
    suggestion: string;
    kind: string;
    entryTitle: string;
    entryDate: string;
    discard: string;
    add: string;
    followUp: string;
    excerpt: string;
    source: string;
  };
  form: FormContent;
}

const de: LandingContent = {
  locale: "de",
  meta: {
    title: "Lernraum – der Schulalltag an einem Ort",
    description:
      "Stundenplan, Hausaufgaben, Prüfungen, Lernpläne und Noten an einem Ort – mit einem Lern-Assistenten, der auf deinem Rechner läuft. Jetzt auf die Warteliste.",
    ogLocale: "de_DE",
  },
  ids: {
    features: "funktionen",
    assistant: "assistent",
    privacy: "datenschutz",
    editions: "varianten",
    waitlist: "warteliste",
    faq: "fragen",
  },
  header: {
    homeLabel: "Lernraum – zum Seitenanfang",
    navLabel: "Seitenbereiche",
    nav: [
      { section: "features", label: "Funktionen" },
      { section: "assistant", label: "Assistent" },
      { section: "privacy", label: "Datenschutz" },
      { section: "editions", label: "Desktop & Server" },
      { section: "faq", label: "Fragen" },
    ],
    waitlist: "Warteliste",
    otherLanguage: { short: "EN", label: "Read this page in English" },
  },
  languageHint: {
    text: "Diese Seite gibt es auch auf Deutsch.",
    action: "Auf Deutsch lesen",
    dismiss: "Hinweis schließen",
  },
  hero: {
    badge: "Bald für Windows und macOS",
    title: "Der Schulalltag an einem Ort.",
    lead: "Stundenplan, Hausaufgaben, Prüfungen, Lernpläne und Noten – dazu ein Lern-Assistent, der deine Unterlagen kennt und auf deinem eigenen Rechner läuft.",
    mascot: "Das Lernraum-Faultier",
  },
  features: {
    title: "Alles, was die Schule verlangt. Nichts, was ablenkt.",
    text: "Lernraum ersetzt das Hausaufgabenheft, den Lernplan auf Papier und die Notizen-App. Jede Funktion hat einen Grund und tut genau das.",
    items: [
      {
        title: "Stundenplan",
        text: "Holt deinen Stundenplan aus WebUntis – einmal am Tag automatisch oder per Knopfdruck. Ausfälle sind sofort sichtbar.",
        points: ["Vier Wochen im Voraus", "Ausfälle und Raumwechsel", "Fächer mit Kürzel und Farbe"],
      },
      {
        title: "Hausaufgaben, Prüfungen, Termine",
        text: "Alles mit Fälligkeit an einem Ort. Das Dashboard beantwortet nur eine Frage: Was steht heute an?",
        points: ["Kennzahlen auf einen Blick", "Erinnerungen per Push", "Offline lesbar"],
      },
      {
        title: "Lernpläne",
        text: "Pläne, Aufgaben und wiederkehrende Serien. Aufgaben können aufeinander warten, und die Warteschlange bleibt sichtbar.",
        points: ["Prioritäten 0–5", "Abhängigkeiten ohne Zyklen", "Täglich, wöchentlich, monatlich"],
      },
      {
        title: "Klausur-Generator",
        text: "Aus einer Prüfung und ihren Themen entsteht ein Lernplan. Das schwächste Thema kommt zuerst, weil es den meisten Vorlauf braucht.",
        points: ["Verteilt bis zum Klausurtag", "Richtet sich nach deinem Lernstand", "Ein zweiter Klick legt nichts doppelt an"],
      },
      {
        title: "Lernfortschritt",
        text: "Fünf Stufen von „unsicher“ bis „gefestigt“, jede mit einem festen Wiederholungsabstand. Erklärbar in einem Satz, kein Black-Box-Algorithmus.",
        points: ["Wiederholung nach 1 bis 30 Tagen", "Lernserie und Wochenziel in Minuten", "Lernzeit je Fach und Wochentag"],
      },
      {
        title: "Noten",
        text: "Einzelnoten mit Gewichtung, Schnitt je Fach und eine Hochrechnung: Was muss noch kommen, damit es reicht? Der Assistent sieht die Noten nur mit deiner Freigabe oder wenn alles lokal läuft.",
        points: ["Gewichteter Schnitt und Verlauf", "Hochrechnung bis zur Bestehensgrenze", "Import aus einer Notenliste"],
      },
      {
        title: "Kalender",
        text: "Termine aus iCalendar, Google Kalender oder Apple Kalender – lesen und, wenn du willst, auch zurückschreiben.",
        points: ["Abo-Link für dein Handy", "Schreiben nur mit Freigabe", "Zugangsdaten verschlüsselt"],
      },
      {
        title: "Unterrichtsmaterial",
        text: "Lade Aufschriebe, Arbeitsblätter und Lösungen hoch, auch als Foto. Lernraum liest sie, erkennt Fach, Thema und Datum und findet sie nach Bedeutung wieder.",
        points: ["Texterkennung für Fotos und Scans", "Suche nach Bedeutung, nicht nur Wörtern", "Korrekturen bleiben erhalten"],
      },
      {
        title: "Belohnungen",
        text: "Punkte gibt es für gelernte Zeit an einem Fach, nicht fürs Abhaken. Gedeckelt pro Tag, damit es ein Anreiz bleibt und kein Wettrennen.",
        points: ["Gestaffelt statt linear", "Tagesgrenze", "Jede Buchung nachvollziehbar"],
      },
    ],
  },
  assistant: {
    title: "Ein Assistent, der deine Unterlagen kennt.",
    text: "Frag nach deinem Aufschrieb, lass dich abfragen oder diktiere die nächste Klausur. Er antwortet aus deinen Dokumenten und sagt ehrlich, wenn er nichts findet.",
    points: [
      {
        title: "Schlägt vor, statt still einzutragen",
        text: "Sagst du „Bio-Klausur nächsten Donnerstag“, entsteht ein Vorschlag mit Datum. Eingetragen wird erst, wenn du auf „Eintragen“ tippst.",
      },
      {
        title: "Fragt dich ab",
        text: "Aus deinem Material entstehen Fragen. Deine Antworten verschieben den Lernstand des Themas – so weiß der Lernplan, wo du stehst.",
      },
      {
        title: "Hört zu und spricht",
        text: "Im Gesprächsmodus bleibt das Mikrofon offen, und die Antworten werden vorgelesen. Erkennung und Stimme laufen lokal mit Whisper und Piper.",
      },
      {
        title: "Zeigt seine Quellen",
        text: "Jede Antwort nennt die Auszüge, auf die sie sich stützt, und ob sie lokal, von einem Online-Dienst oder ohne Modell entstanden ist.",
      },
    ],
    learnsTitle: "Lernt an dir – auf drei Ebenen",
    level: "Ebene {n}",
    levels: [
      {
        title: "Notizen",
        text: "Sagst du „Merke: Ich lerne abends besser“, schreibt der Assistent es auf. Jede Notiz siehst du und kannst sie löschen.",
      },
      {
        title: "Lernprofil",
        text: "Unsichere und fällige Themen, Lernzeiten, Abfrageergebnisse – nur aus deinen Daten gerechnet, nie geraten.",
      },
      {
        title: "Persönliches Training",
        text: "Mit Freigabe und passender Grafikkarte lernt ein kleines Zusatzmodell deinen Stil. Es wird nur aktiviert, wenn es nachweislich besser ist.",
      },
    ],
  },
  privacy: {
    title: "Gebaut für Schülerdaten.",
    text: "Noten, Stundenplan und Aufschriebe sind persönlich. Deshalb ist der Normalfall in Lernraum: alles bleibt auf deinem Gerät.",
    items: [
      {
        title: "Lokal zuerst",
        text: "Sprachmodell, Spracherkennung und Vorlesen laufen auf deinem Rechner. Ein Online-Dienst ist optional, und jede Antwort sagt, woher sie kommt.",
      },
      {
        title: "Deine Daten bleiben bei dir",
        text: "Die Desktop-App speichert alles in deinem Benutzerordner. Kein Konto, keine Cloud, kein Tracking. Gespräche liegen nur im Browser.",
      },
      {
        title: "Freigaben sind aus",
        text: "Noten teilen, Kalender beschreiben, aus Gesprächen lernen: alles ist standardmäßig aus und jederzeit widerrufbar.",
      },
      {
        title: "Verschlüsselte Zugangsdaten",
        text: "Passwörter für WebUntis, Kalender und KI-Dienste werden mit AES-GCM verschlüsselt gespeichert und nur im Moment des Abrufs entschlüsselt.",
      },
    ],
  },
  editions: {
    title: "Als App oder auf dem eigenen Server.",
    text: "Dieselbe Oberfläche, dieselben Funktionen. Die Desktop-App ist für eine Person gedacht, der Server für alle, die mehrere Konten selbst betreiben möchten.",
    desktop: "Desktop-App",
    server: "Server",
    rows: [
      ["Für wen", "Eine Person an ihrem Rechner", "Familien, Lerngruppen, Selbsthoster"],
      ["Installation", "Installer starten, Namen eingeben", "Docker Compose"],
      ["Anmeldung", "Keine", "Benutzername und Passwort"],
      ["Daten", "SQLite im Benutzerordner", "PostgreSQL mit pgvector"],
      ["KI-Modelle", "Eingebaut, Download in der App", "Eigener Ollama-, LM-Studio- oder llama.cpp-Server"],
      ["Läuft auf", "Windows x64, macOS (Apple Silicon und Intel)", "Linux mit Docker (x64, ARM64 in Vorbereitung)"],
    ],
  },
  waitlist: {
    title: "Sei dabei, wenn es losgeht.",
    text: "Die ersten Installer verschicken wir in kleinen Runden an die Warteliste. Sag uns, wofür du Lernraum nutzen willst – das entscheidet, welche Plattform zuerst fertig wird.",
    facts: [
      { label: "Plattformen", value: "Windows, macOS" },
      { label: "Konto", value: "Nicht nötig" },
      { label: "E-Mails", value: "Eine zum Start" },
    ],
    mascotNote: "Das Faultier wartet geduldig mit.",
  },
  faq: {
    title: "Häufige Fragen",
    text: "Was du vor dem Eintragen wissen solltest.",
    items: [
      {
        question: "Was kostet Lernraum?",
        answer:
          "Die Preise geben wir zum Start bekannt – die Warteliste erfährt es zuerst. Einen Online-KI-Dienst brauchst du nicht; bindest du einen an, gelten dessen Preise.",
      },
      {
        question: "Brauche ich eine Internetverbindung?",
        answer:
          "Nur für den Stundenplan-Abruf, Kalender und das einmalige Herunterladen der Modelle. Danach funktioniert Lernraum inklusive Assistent offline.",
      },
      {
        question: "Welcher Rechner reicht?",
        answer:
          "Windows (x64) oder macOS (Apple Silicon und Intel). Wie viel Arbeitsspeicher ein lokales Modell braucht, zeigt die App vor dem Herunterladen an. Das persönliche Training braucht eine NVIDIA-Grafikkarte ab 6 GB oder einen Mac mit Apple Silicon ab 16 GB.",
      },
      {
        question: "Muss meine Schule WebUntis nutzen?",
        answer:
          "Nein. Du kannst den Stundenplan auch selbst eintragen oder als PDF oder Foto hochladen; Lernraum schlägt dann einen Plan vor, den du vor dem Übernehmen prüfst. WebUntis ist nur der bequemste Weg.",
      },
      {
        question: "Gibt es Lernraum auch auf Englisch?",
        answer:
          "Ja. Die App lässt sich zwischen Deutsch und Englisch umschalten, und der Assistent antwortet in der eingestellten Sprache.",
      },
      {
        question: "Was passiert mit meiner E-Mail-Adresse?",
        answer:
          "Wir benutzen sie genau einmal: um dir zu schreiben, wenn Lernraum verfügbar ist. Sie wird nicht weitergegeben. Eine kurze Antwort auf die Mail genügt, und wir löschen sie.",
      },
      {
        question: "Kann ich Lernraum selbst hosten?",
        answer:
          "Ja. Die Server-Variante läuft mit Docker Compose auf einem eigenen Rechner oder Heimserver, mit PostgreSQL und Anmeldung für mehrere Personen.",
      },
    ],
  },
  footer: {
    tagline: "Lernraum · Der Schulalltag an einem Ort.",
    navLabel: "Fußzeile",
    links: [
      { section: "features", label: "Funktionen" },
      { section: "privacy", label: "Datenschutz" },
      { section: "waitlist", label: "Warteliste" },
    ],
  },
  appPreview: {
    label: "Vorschau des Lernraum-Dashboards",
    nav: [
      { group: "Heute", items: ["Übersicht", "Stundenplan", "Hausaufgaben", "Prüfungen", "Kalender"] },
      { group: "Bibliothek", items: ["Lernen", "Fächer", "Dokumente", "Fortschritt"] },
      { group: "Werkzeuge", items: ["Assistent", "KI & Modelle"] },
    ],
    active: "Übersicht",
    greeting: "Guten Morgen, Mia",
    summary: "Mathe um 09:35, danach Bio. Die Klausur ist in neun Tagen.",
    source: "lokal",
    stats: [
      { title: "Nächste Stunde", value: "09:35", note: "Mathe · R 204" },
      { title: "Hausaufgaben", value: "3", note: "1 bis morgen" },
      { title: "Prüfungen", value: "Bio", note: "in 9 Tagen" },
      { title: "Lernzeit", value: "2:40 h", note: "von 5 h Wochenziel" },
    ],
    today: "Heute",
    lessons: [
      { time: "07:45", short: "D", name: "Deutsch", room: "R 112" },
      { time: "08:35", short: "E", name: "Englisch", room: "R 112" },
      { time: "09:35", short: "M", name: "Mathematik", room: "R 204", now: true },
      { time: "10:25", short: "Bio", name: "Biologie", room: "Bio 2" },
      { time: "11:30", short: "F", name: "Französisch", room: "R 017", cancelled: true },
    ],
    cancelled: "entfällt",
    homework: "Hausaufgaben",
    tasks: [
      { title: "Arbeitsblatt Ableitungen", subject: "M", due: "morgen", done: false },
      { title: "Vokabeln Unit 4", subject: "E", due: "Do", done: false },
      { title: "Gedichtanalyse – Entwurf", subject: "D", due: "Fr", done: true },
    ],
  },
  assistantPreview: {
    label: "Beispielgespräch mit dem Lern-Assistenten",
    question: "Bio-Klausur ist nächsten Donnerstag. Trag sie ein und frag mich zur Zellatmung ab.",
    answer:
      "Mach ich. Laut deinem Aufschrieb vom 03.10. sind Glykolyse und Atmungskette die zwei großen Blöcke – wir fangen mit der Glykolyse an, da warst du zuletzt unsicher.",
    suggestion: "Vorschlag",
    kind: "Prüfung",
    entryTitle: "Klausur Biologie",
    entryDate: "Do, 15.10. · 08:00",
    discard: "Verwerfen",
    add: "Eintragen",
    followUp: "Erste Frage: Wo in der Zelle findet die Glykolyse statt, und was kommt am Ende heraus?",
    excerpt: "Aufschrieb · Bio · Zellatmung · 03.10.2026",
    source: "lokal · Qwen2.5 3B",
  },
  form: {
    emailLabel: "E-Mail-Adresse",
    emailPlaceholder: "deine@email.de",
    nameLabel: "Name",
    optional: "optional",
    roleLabel: "Ich bin",
    choose: "Bitte wählen",
    roles: { schueler: "Schüler:in", eltern: "Elternteil", lehrkraft: "Lehrkraft", andere: "Etwas anderes" },
    platformLegend: "Wo möchtest du Lernraum nutzen?",
    platforms: { windows: "Windows", macos: "macOS", server: "Eigener Server" },
    consent:
      "Ihr dürft mir eine E-Mail schicken, sobald Lernraum verfügbar ist. Keine Werbung, Abmeldung jederzeit. Unter 16? Dann frag bitte vorher deine Eltern.",
    submit: "Auf die Warteliste",
    sending: "Wird eingetragen …",
    doneTitle: "Eingetragen.",
    doneMessage: "Danke! Du stehst auf der Warteliste. Wir melden uns zum Start.",
    honeypotLabel: "Website",
    errors: {
      emailMissing: "Bitte gib deine E-Mail-Adresse an.",
      emailInvalid: "Diese E-Mail-Adresse sieht nicht gültig aus.",
      name: "Der Name darf höchstens 80 Zeichen haben.",
      role: "Bitte wähle eine der angebotenen Rollen.",
      platform: "Bitte wähle eine der angebotenen Plattformen.",
      consent: "Bitte bestätige, dass wir dir zum Start eine E-Mail schicken dürfen.",
      unreadable: "Die Anfrage war leer oder unlesbar.",
      forbidden: "Diese Anfrage kommt nicht von der Lernraum-Seite.",
      tooLarge: "Die Anfrage ist zu groß.",
      rateLimited: "Zu viele Versuche. Bitte versuche es in ein paar Minuten noch einmal.",
      unavailable: "Die Warteliste ist gerade nicht erreichbar. Bitte versuche es später noch einmal.",
      generic: "Das hat nicht geklappt. Bitte versuche es noch einmal.",
      offline: "Keine Verbindung. Bitte prüfe dein Netz und versuche es erneut.",
    },
  },
};

const en: LandingContent = {
  locale: "en",
  meta: {
    title: "Lernraum – your school day in one place",
    description:
      "Timetable, homework, exams, study plans and grades in one place – with a study assistant that runs on your own computer. Join the waitlist.",
    ogLocale: "en_GB",
  },
  ids: {
    features: "features",
    assistant: "assistant",
    privacy: "privacy",
    editions: "editions",
    waitlist: "waitlist",
    faq: "faq",
  },
  header: {
    homeLabel: "Lernraum – back to top",
    navLabel: "Page sections",
    nav: [
      { section: "features", label: "Features" },
      { section: "assistant", label: "Assistant" },
      { section: "privacy", label: "Privacy" },
      { section: "editions", label: "Desktop & server" },
      { section: "faq", label: "FAQ" },
    ],
    waitlist: "Waitlist",
    otherLanguage: { short: "DE", label: "Diese Seite auf Deutsch lesen" },
  },
  languageHint: {
    text: "This page is also available in English.",
    action: "Read in English",
    dismiss: "Dismiss",
  },
  hero: {
    badge: "Coming soon for Windows and macOS",
    title: "Your school day in one place.",
    lead: "Timetable, homework, exams, study plans and grades – plus a study assistant that knows your notes and runs on your own computer.",
    mascot: "The Lernraum sloth",
  },
  features: {
    title: "Everything school asks of you. Nothing that distracts.",
    text: "Lernraum replaces your homework diary, the study plan on paper and your notes app. Every feature has a reason and does exactly that.",
    items: [
      {
        title: "Timetable",
        text: "Pulls your timetable from WebUntis – automatically once a day or at the press of a button. Cancelled lessons show up right away.",
        points: ["Four weeks ahead", "Cancellations and room changes", "Subjects with short name and colour"],
      },
      {
        title: "Homework, exams, events",
        text: "Everything with a due date in one place. The dashboard answers a single question: what's on today?",
        points: ["Key figures at a glance", "Push reminders", "Readable offline"],
      },
      {
        title: "Study plans",
        text: "Plans, tasks and recurring series. Tasks can wait for each other, and the queue stays visible.",
        points: ["Priorities 0–5", "Dependencies without cycles", "Daily, weekly, monthly"],
      },
      {
        title: "Exam planner",
        text: "An exam and its topics become a study plan. The weakest topic comes first, because it needs the most lead time.",
        points: ["Spread out until exam day", "Follows your current progress", "A second click never creates duplicates"],
      },
      {
        title: "Learning progress",
        text: "Five levels from “unsure” to “mastered”, each with a fixed review interval. Explained in one sentence – no black-box algorithm.",
        points: ["Review after 1 to 30 days", "Study streak and weekly goal in minutes", "Study time per subject and weekday"],
      },
      {
        title: "Grades",
        text: "Individual grades with weighting, an average per subject and a projection: what do you still need to pass? The assistant only sees your grades if you allow it or everything runs locally.",
        points: ["Weighted average and history", "Projection to the pass mark", "Import from a grade list"],
      },
      {
        title: "Calendar",
        text: "Events from iCalendar, Google Calendar or Apple Calendar – read them and, if you like, write back.",
        points: ["Subscription link for your phone", "Writing only with permission", "Credentials encrypted"],
      },
      {
        title: "Class materials",
        text: "Upload notes, worksheets and solutions, photos included. Lernraum reads them, recognises subject, topic and date, and finds them again by meaning.",
        points: ["Text recognition for photos and scans", "Search by meaning, not just words", "Your corrections are kept"],
      },
      {
        title: "Rewards",
        text: "Points are earned for time spent studying a subject, not for ticking boxes. Capped per day so it stays an incentive, not a race.",
        points: ["Tiered, not linear", "Daily cap", "Every entry traceable"],
      },
    ],
  },
  assistant: {
    title: "An assistant that knows your notes.",
    text: "Ask about your class notes, get quizzed or dictate your next exam. It answers from your documents and tells you honestly when it finds nothing.",
    points: [
      {
        title: "Suggests instead of silently adding",
        text: "Say “biology exam next Thursday” and you get a suggestion with a date. Nothing is added until you tap “Add”.",
      },
      {
        title: "Quizzes you",
        text: "Questions are generated from your material. Your answers move the topic's progress – so the study plan knows where you stand.",
      },
      {
        title: "Listens and speaks",
        text: "In conversation mode the microphone stays open and answers are read aloud. Recognition and voice run locally with Whisper and Piper.",
      },
      {
        title: "Shows its sources",
        text: "Every answer names the excerpts it relies on, and whether it came from a local model, an online service or no model at all.",
      },
    ],
    learnsTitle: "Learns from you – on three levels",
    level: "Level {n}",
    levels: [
      {
        title: "Notes",
        text: "Say “Remember: I study better in the evening” and the assistant writes it down. You can see and delete every note.",
      },
      {
        title: "Learner profile",
        text: "Unsure and due topics, study times, quiz results – calculated from your data only, never guessed.",
      },
      {
        title: "Personal training",
        text: "With your permission and a suitable graphics card, a small add-on model learns your style. It is only switched on if it is measurably better.",
      },
    ],
  },
  privacy: {
    title: "Built for student data.",
    text: "Grades, timetables and class notes are personal. That's why the default in Lernraum is: everything stays on your device.",
    items: [
      {
        title: "Local first",
        text: "Language model, speech recognition and read-aloud run on your computer. An online service is optional, and every answer says where it came from.",
      },
      {
        title: "Your data stays with you",
        text: "The desktop app stores everything in your user folder. No account, no cloud, no tracking. Conversations live only in your browser.",
      },
      {
        title: "Sharing is off",
        text: "Sharing grades, writing to calendars, learning from conversations: everything is off by default and can be revoked at any time.",
      },
      {
        title: "Encrypted credentials",
        text: "Passwords for WebUntis, calendars and AI services are stored encrypted with AES-GCM and only decrypted at the moment they're used.",
      },
    ],
  },
  editions: {
    title: "As an app or on your own server.",
    text: "Same interface, same features. The desktop app is meant for one person, the server for anyone who wants to run several accounts themselves.",
    desktop: "Desktop app",
    server: "Server",
    rows: [
      ["For whom", "One person on their computer", "Families, study groups, self-hosters"],
      ["Installation", "Run the installer, enter your name", "Docker Compose"],
      ["Sign-in", "None", "Username and password"],
      ["Data", "SQLite in your user folder", "PostgreSQL with pgvector"],
      ["AI models", "Built in, downloaded in the app", "Your own Ollama, LM Studio or llama.cpp server"],
      ["Runs on", "Windows x64, macOS (Apple Silicon and Intel)", "Linux with Docker (x64, ARM64 in progress)"],
    ],
  },
  waitlist: {
    title: "Be there when it starts.",
    text: "We'll send the first installers to the waitlist in small batches. Tell us what you want to use Lernraum for – that decides which platform is ready first.",
    facts: [
      { label: "Platforms", value: "Windows, macOS" },
      { label: "Account", value: "Not needed" },
      { label: "Emails", value: "One at launch" },
    ],
    mascotNote: "The sloth is waiting patiently with you.",
  },
  faq: {
    title: "Frequently asked questions",
    text: "What you should know before signing up.",
    items: [
      {
        question: "How much does Lernraum cost?",
        answer:
          "We'll announce pricing at launch – the waitlist hears first. You don't need an online AI service; if you connect one, its own pricing applies.",
      },
      {
        question: "Do I need an internet connection?",
        answer:
          "Only for fetching the timetable, calendars and the one-time model download. After that, Lernraum works offline, assistant included.",
      },
      {
        question: "What kind of computer do I need?",
        answer:
          "Windows (x64) or macOS (Apple Silicon and Intel). The app shows how much memory a local model needs before you download it. Personal training needs an NVIDIA graphics card with 6 GB or more, or a Mac with Apple Silicon and 16 GB or more.",
      },
      {
        question: "Does my school have to use WebUntis?",
        answer:
          "No. You can enter your timetable yourself or upload it as a PDF or photo; Lernraum then suggests a plan that you check before accepting it. WebUntis is just the most convenient way.",
      },
      {
        question: "Is Lernraum available in English?",
        answer:
          "Yes. The app can be switched between German and English, and the assistant replies in the language you choose.",
      },
      {
        question: "What happens to my email address?",
        answer:
          "We use it exactly once: to write to you when Lernraum is available. It is never passed on. A short reply to that email is enough and we'll delete it.",
      },
      {
        question: "Can I host Lernraum myself?",
        answer:
          "Yes. The server edition runs with Docker Compose on your own computer or home server, with PostgreSQL and sign-in for several people.",
      },
    ],
  },
  footer: {
    tagline: "Lernraum · Your school day in one place.",
    navLabel: "Footer",
    links: [
      { section: "features", label: "Features" },
      { section: "privacy", label: "Privacy" },
      { section: "waitlist", label: "Waitlist" },
    ],
  },
  appPreview: {
    label: "Preview of the Lernraum dashboard",
    nav: [
      { group: "Today", items: ["Overview", "Timetable", "Homework", "Exams", "Calendar"] },
      { group: "Library", items: ["Study", "Subjects", "Documents", "Progress"] },
      { group: "Tools", items: ["Assistant", "AI & models"] },
    ],
    active: "Overview",
    greeting: "Good morning, Mia",
    summary: "Maths at 09:35, then biology. The exam is in nine days.",
    source: "local",
    stats: [
      { title: "Next lesson", value: "09:35", note: "Maths · R 204" },
      { title: "Homework", value: "3", note: "1 due tomorrow" },
      { title: "Exams", value: "Bio", note: "in 9 days" },
      { title: "Study time", value: "2:40 h", note: "of 5 h weekly goal" },
    ],
    today: "Today",
    lessons: [
      { time: "07:45", short: "Ger", name: "German", room: "R 112" },
      { time: "08:35", short: "Eng", name: "English", room: "R 112" },
      { time: "09:35", short: "Ma", name: "Maths", room: "R 204", now: true },
      { time: "10:25", short: "Bio", name: "Biology", room: "Bio 2" },
      { time: "11:30", short: "Fr", name: "French", room: "R 017", cancelled: true },
    ],
    cancelled: "cancelled",
    homework: "Homework",
    tasks: [
      { title: "Worksheet: derivatives", subject: "Ma", due: "tomorrow", done: false },
      { title: "Vocabulary unit 4", subject: "Eng", due: "Thu", done: false },
      { title: "Poem analysis – draft", subject: "Ger", due: "Fri", done: true },
    ],
  },
  assistantPreview: {
    label: "Example conversation with the study assistant",
    question: "Biology exam is next Thursday. Add it and quiz me on cellular respiration.",
    answer:
      "Will do. According to your notes from 3 October, glycolysis and the electron transport chain are the two big blocks – let's start with glycolysis, you were unsure about it last time.",
    suggestion: "Suggestion",
    kind: "Exam",
    entryTitle: "Biology exam",
    entryDate: "Thu, 15 Oct · 08:00",
    discard: "Discard",
    add: "Add",
    followUp: "First question: where in the cell does glycolysis take place, and what comes out at the end?",
    excerpt: "Notes · Bio · Cellular respiration · 3 Oct 2026",
    source: "local · Qwen2.5 3B",
  },
  form: {
    emailLabel: "Email address",
    emailPlaceholder: "you@example.com",
    nameLabel: "Name",
    optional: "optional",
    roleLabel: "I am",
    choose: "Please choose",
    roles: { schueler: "A student", eltern: "A parent", lehrkraft: "A teacher", andere: "Something else" },
    platformLegend: "Where would you like to use Lernraum?",
    platforms: { windows: "Windows", macos: "macOS", server: "Own server" },
    consent:
      "You may email me once Lernraum is available. No advertising, unsubscribe at any time. Under 16? Please ask your parents first.",
    submit: "Join the waitlist",
    sending: "Signing you up …",
    doneTitle: "You're on the list.",
    doneMessage: "Thank you! You're on the waitlist. We'll be in touch at launch.",
    honeypotLabel: "Website",
    errors: {
      emailMissing: "Please enter your email address.",
      emailInvalid: "This email address doesn't look valid.",
      name: "The name can be at most 80 characters long.",
      role: "Please choose one of the roles offered.",
      platform: "Please choose one of the platforms offered.",
      consent: "Please confirm that we may email you at launch.",
      unreadable: "The request was empty or unreadable.",
      forbidden: "This request didn't come from the Lernraum page.",
      tooLarge: "The request is too large.",
      rateLimited: "Too many attempts. Please try again in a few minutes.",
      unavailable: "The waitlist is unavailable right now. Please try again later.",
      generic: "That didn't work. Please try again.",
      offline: "No connection. Please check your network and try again.",
    },
  },
};

export const CONTENT: Record<Locale, LandingContent> = { de, en };

/** Pfad der Startseite je Sprache, ohne `basePath`. */
export function homePath(locale: Locale): string {
  return locale === "de" ? "/" : `/${locale}/`;
}

/*
 * Adresse fuer Sprachlinks, mit `basePath`. Bewusst ein schlichtes `<a>` statt
 * `next/link`: zwischen den beiden Wurzel-Layouts laedt Next ohnehin die ganze
 * Seite neu, und das Vorladen fragte im statischen Export eine Datei der
 * Routengruppe `(de)` an, die es so nicht gibt (404).
 */
export function homeHref(locale: Locale): string {
  return `${process.env.NEXT_PUBLIC_BASE_PATH ?? ""}${homePath(locale)}`;
}

export function otherLocale(locale: Locale): Locale {
  return locale === "de" ? "en" : "de";
}
