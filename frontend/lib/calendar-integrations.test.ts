import { describe, expect, it } from "vitest";
import {
  buildIntegrationWrite,
  fieldLabel,
  integrationConfigFields,
  isReadOnlyCredential,
  isSecretField,
  isSyncableKind,
  isWritingBlocked,
  needsAuthorization,
  syncLabelFor,
  validateIntegrationInput,
} from "./calendar-integrations";
import type { IntegrationCatalog, IntegrationRead } from "./types";

function spec(overrides: Partial<IntegrationCatalog> = {}): IntegrationCatalog {
  return {
    kind: "ical",
    label: "iCalendar (ICS)",
    description: "Liest Termine aus einer abonnierten Adresse.",
    required_config: [],
    required_one_of: [["url", "path"]],
    required_credentials: [],
    optional_credentials: [],
    supports_writing: false,
    ...overrides,
  };
}

function verbindung(overrides: Partial<IntegrationRead> = {}): IntegrationRead {
  return {
    id: 1,
    kind: "ical",
    name: "Abo",
    enabled: true,
    status: "configured",
    config: {},
    has_credentials: false,
    credential_keys: [],
    missing_fields: [],
    created_at: "2026-09-30T00:00:00Z",
    updated_at: "2026-09-30T00:00:00Z",
    ...overrides,
  };
}

describe("Abruf-Knopf je Integrationsart", () => {
  it("bietet für alle abrufbaren Arten einen Abruf an", () => {
    for (const kind of ["webuntis", "documents", "ical", "google_calendar", "caldav"]) {
      expect(isSyncableKind(kind)).toBe(true);
    }
  });

  it("bietet für einen Dienst ohne Abruf keinen Knopf an", () => {
    // `ai_provider` wird geprüft und geholt, aber nicht abgerufen. Ein Knopf,
    // der dann nichts tut, wäre schlechter als keiner.
    expect(isSyncableKind("ai_provider")).toBe(false);
  });

  it("nennt den Abruf passend zur Art", () => {
    expect(syncLabelFor("webuntis")).toBe("Stundenplan aktualisieren");
    expect(syncLabelFor("ical")).toBe("Termine holen");
    expect(syncLabelFor("caldav")).toBe("Termine abgleichen");
    expect(syncLabelFor("google_calendar")).toBe("Termine abgleichen");
    expect(syncLabelFor("unbekannt")).toBe("Aktualisieren");
  });
});

describe("Zwei Angaben, von denen eine reicht", () => {
  it("akzeptiert eine Adresse allein", () => {
    expect(validateIntegrationInput(spec(), { url: "https://a.example/k.ics" }, {})).toBeNull();
  });

  it("akzeptiert einen Pfad allein", () => {
    expect(validateIntegrationInput(spec(), { path: "schule.ics" }, {})).toBeNull();
  });

  it("nennt beide als eine einzige Lücke", () => {
    const fehler = validateIntegrationInput(spec(), {}, {});
    expect(fehler?.message).toBe("Diese Angaben fehlen noch: ICS-Adresse oder Ablageort.");
  });

  it("lehnt zwei gefüllte Felder derselben Gruppe ab", () => {
    const fehler = validateIntegrationInput(spec(), { url: "https://a.example/k.ics", path: "schule.ics" }, {});
    expect(fehler?.message).toBe("Trage nur eine Angabe ein: ICS-Adresse oder Ablageort.");
    expect(fehler?.invalid).toEqual(["url", "path"]);
  });

  it("behandelt ein Feld mit nur Leerzeichen als leer", () => {
    expect(validateIntegrationInput(spec(), { url: "   " }, {})?.message).toContain("fehlen noch");
  });
});

describe("Pflichtfelder einer Google-Verbindung", () => {
  const google = spec({
    kind: "google_calendar",
    label: "Google Kalender",
    required_config: ["client_id", "calendar_id", "redirect_uri"],
    required_one_of: [],
    required_credentials: ["client_secret"],
    optional_credentials: ["refresh_token", "token_scope"],
    supports_writing: true,
  });

  it("nennt alle fehlenden Konfigurationsfelder", () => {
    const fehler = validateIntegrationInput(google, {}, {});
    expect(fehler?.invalid).toEqual(["client_id", "calendar_id", "redirect_uri", "client_secret"]);
    expect(fehler?.message).toContain("Client-ID");
    expect(fehler?.message).toContain("Weiterleitungs-Adresse");
    expect(fehler?.message).toContain("Client-Schlüssel");
  });

  it("akzeptiert eine vollständige Verbindung", () => {
    const fehler = validateIntegrationInput(
      google,
      { client_id: "id", calendar_id: "primary", redirect_uri: "https://a.example/r" },
      { client_secret: "geheim" },
    );
    expect(fehler).toBeNull();
  });
});

describe("Was das Formular abschickt", () => {
  it("nimmt das alternative Feld mit, auch wenn es kein Pflichtfeld ist", () => {
    const eingabe = buildIntegrationWrite(spec(), "", { path: "  schule.ics  " }, {});
    expect(eingabe.config).toEqual({ path: "schule.ics" });
    expect(eingabe.name).toBe("iCalendar (ICS)");
  });

  it("schickt keinen leeren Refresh-Token mit", () => {
    /*
      Das ist der eigentliche Grund fuer die Liste der schreibgeschuetzten
      Felder: schickte das Formular einen leeren `refresh_token`, wuerde der
      PATCH die gespeicherte Credential-Blase ueberschreiben und damit die
      erteilte Zustimmung aus der Einwilligung loeschen.
    */
    const google = spec({
      kind: "google_calendar",
      label: "Google Kalender",
      required_config: ["client_id"],
      required_one_of: [],
      required_credentials: ["client_secret"],
      optional_credentials: ["refresh_token", "token_scope"],
      supports_writing: true,
    });
    const eingabe = buildIntegrationWrite(
      google,
      "Google",
      { client_id: "id" },
      { client_secret: "geheim", refresh_token: "", token_scope: "" },
    );
    expect(eingabe.credentials).toEqual({ client_secret: "geheim" });
  });

  it("haelt den Namen aus dem Feld, sonst den des Katalogs", () => {
    expect(buildIntegrationWrite(spec(), "  Schulplan  ", { url: "https://a.example/k.ics" }, {}).name).toBe(
      "Schulplan",
    );
    expect(buildIntegrationWrite(spec(), "   ", { url: "https://a.example/k.ics" }, {}).name).toBe("iCalendar (ICS)");
  });
});

describe("Die Einwilligung von Google", () => {
  it("fehlt, solange kein Refresh-Token hinterlegt ist", () => {
    expect(needsAuthorization(verbindung({ kind: "google_calendar" }))).toBe(true);
    expect(needsAuthorization(verbindung({ kind: "google_calendar", credential_keys: ["client_secret"] }))).toBe(true);
  });

  it("liegt vor, sobald der Rückweg gelaufen ist", () => {
    expect(
      needsAuthorization(verbindung({ kind: "google_calendar", credential_keys: ["client_secret", "refresh_token"] })),
    ).toBe(false);
  });

  it("gilt nur für Google", () => {
    expect(needsAuthorization(verbindung({ kind: "ical" }))).toBe(false);
    expect(needsAuthorization(verbindung({ kind: "caldav" }))).toBe(false);
  });
});

describe("Lesen und Schreiben", () => {
  const katalog = [
    spec({ kind: "ical", supports_writing: false }),
    spec({ kind: "caldav", label: "Apple Kalender (CalDAV)", supports_writing: true }),
  ];

  it("sperrt das Schreiben ohne Zustimmung nur dort, wo es möglich wäre", () => {
    expect(isWritingBlocked("caldav", katalog, false)).toBe(true);
    expect(isWritingBlocked("caldav", katalog, true)).toBe(false);
    // Ein Abo kann ohnehin nicht beschrieben werden - die Zustimmung ändert
    // daran nichts, also gibt es hier auch nichts zu sperren.
    expect(isWritingBlocked("ical", katalog, false)).toBe(false);
  });

  it("kennt eine unbekannte Art nicht als beschreibbar", () => {
    expect(isWritingBlocked("webuntis", katalog, false)).toBe(false);
  });
});

describe("Geheimnisse und Felder, die der Nutzer nicht setzt", () => {
  it("behandelt Passwörter und Schlüssel als geheim", () => {
    expect(isSecretField("password")).toBe(true);
    expect(isSecretField("api_key")).toBe(true);
    expect(isSecretField("client_secret")).toBe(true);
    expect(isSecretField("school")).toBe(false);
  });

  it("schützt den Refresh-Token vor der Hand", () => {
    expect(isReadOnlyCredential("refresh_token")).toBe(true);
    expect(isReadOnlyCredential("token_scope")).toBe(true);
    expect(isReadOnlyCredential("client_secret")).toBe(false);
  });

  it("übersetzt die Feldnamen und lässt Unbekanntes stehen", () => {
    expect(fieldLabel("client_id")).toBe("Client-ID");
    expect(fieldLabel("redirect_uri")).toBe("Weiterleitungs-Adresse");
    expect(fieldLabel("runtime")).toBe("Laufzeit");
    expect(fieldLabel("etwas_neues")).toBe("etwas_neues");
  });
});

describe("Das optionale Feld runtime beim KI-Server", () => {
  const ki = spec({
    kind: "ai_provider",
    label: "KI-Server",
    required_config: ["base_url", "model"],
    required_one_of: [],
    optional_config: ["runtime"],
  });

  it("zeigt Pflichtfelder zuerst und das optionale danach", () => {
    // Die Reihenfolge ist die Anzeigereihenfolge; das optionale Feld haengt
    // sich hinten an, damit es nicht zwischen zwei Pflichtfeldern steht.
    expect(integrationConfigFields(ki)).toEqual(["base_url", "model", "runtime"]);
  });

  it("kommt auch ohne das Feld aus, weil ein Eintrag von heute es nicht hat", () => {
    const alt = spec({ kind: "ai_provider", required_config: ["base_url"], required_one_of: [] });
    expect(integrationConfigFields(alt)).toEqual(["base_url"]);
    expect(buildIntegrationWrite(alt, "KI", { base_url: "https://api.example.com/v1" }, {}).config).toEqual({
      base_url: "https://api.example.com/v1",
    });
  });

  it("schickt die gewaehlte Laufzeit mit", () => {
    const eingabe = buildIntegrationWrite(
      ki,
      "Ollama",
      { base_url: "http://host.docker.internal:11434", model: "llama3.2", runtime: "ollama" },
      {},
    );
    expect(eingabe.config).toEqual({
      base_url: "http://host.docker.internal:11434",
      model: "llama3.2",
      runtime: "ollama",
    });
  });

  it("verlangt runtime nicht, sondern schickt es nur mit, wenn es gesetzt ist", () => {
    expect(validateIntegrationInput(ki, { base_url: "http://127.0.0.1:11434", model: "Auto" }, {})).toBeNull();
    expect(buildIntegrationWrite(ki, "Ollama", { base_url: "http://127.0.0.1:11434", model: "Auto" }, {}).config).toEqual({
      base_url: "http://127.0.0.1:11434",
      model: "Auto",
    });
  });
});
