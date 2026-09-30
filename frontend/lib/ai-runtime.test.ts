import { describe, expect, it } from "vitest";
import {
  CONTAINER_HOST,
  containerTrapNote,
  describeSource,
  describeStages,
  dockerHostFor,
  formatRuntimePort,
  hostOf,
  isContainerLoopback,
  isLocalServer,
  normalizeSource,
  parseAiRuntime,
  presetFields,
  presetFor,
  runtimeOptions,
  type AiRuntimePayload,
} from "./ai-runtime";

const ANTWORT = {
  runtimes: [
    {
      kind: "ollama",
      label: "Ollama",
      port: 11434,
      base_url: `http://${CONTAINER_HOST}:11434`,
      native_base_url: "http://127.0.0.1:11434",
      default_model: "llama3.2",
      needs_api_key: false,
      note: "Ollama laeuft als Dienst auf deinem Rechner.",
    },
    {
      kind: "lm_studio",
      label: "LM Studio",
      port: 1234,
      base_url: `http://${CONTAINER_HOST}:1234`,
      native_base_url: "http://127.0.0.1:1234",
      default_model: "Auto",
      needs_api_key: false,
      note: "LM Studio muss im Reiter 'Developer' laufen.",
    },
    {
      kind: "llama_cpp",
      label: "llama.cpp (llama-server)",
      port: 8080,
      base_url: `http://${CONTAINER_HOST}:8080`,
      native_base_url: "http://127.0.0.1:8080",
      default_model: "Auto",
      needs_api_key: false,
      note: "llama-server aus llama.cpp.",
    },
  ],
  runtime_values: ["auto", "ollama", "lm_studio", "llama_cpp", "online"],
  runtime_labels: {
    auto: "Automatisch aus der Adresse",
    ollama: "Ollama",
    lm_studio: "LM Studio",
    llama_cpp: "llama.cpp (llama-server)",
    online: "Online-Anbieter",
  },
  container_host: CONTAINER_HOST,
  container_loopback_hosts: ["0.0.0.0", "::", "::1", "localhost"],
  container_trap_hint: "localhost zeigt auf den Container.",
  stages: [
    "1. Ein lokaler Server auf diesem Rechner (Ollama, LM Studio, llama.cpp).",
    "2. Sonst der eingetragene Online-Provider.",
    "3. Sonst der Satz aus deinen Daten - ohne KI, aber nie leer.",
  ],
  local_first: true,
};

const RUNTIME: AiRuntimePayload = {
  runtimes: [],
  runtime_values: [],
  runtime_labels: {},
  container_host: CONTAINER_HOST,
  container_loopback_hosts: [],
  container_trap_hint: "",
  stages: [],
  local_first: true,
};

describe("Laufzeiten aus der Antwort lesen", () => {
  it("liest alle drei lokalen Laufzeiten", () => {
    const geladen = parseAiRuntime(ANTWORT);
    expect(geladen).not.toBeNull();
    expect(geladen?.runtimes.map((preset) => preset.kind)).toEqual(["ollama", "lm_studio", "llama_cpp"]);
    expect(geladen?.runtimes[0].port).toBe(11434);
  });

  it("nennt beide Adressen, weil die Bauart nicht bekannt ist", () => {
    const geladen = parseAiRuntime(ANTWORT);
    expect(geladen?.runtimes[0].base_url).toBe(`http://${CONTAINER_HOST}:11434`);
    expect(geladen?.runtimes[0].native_base_url).toBe("http://127.0.0.1:11434");
  });

  it("verwirft ein Preset ohne Art, statt eine halbe Zeile zu zeigen", () => {
    const ohneArt = parseAiRuntime({ runtimes: [{ label: "Namenlos", port: 1 }], runtime_values: [] });
    expect(ohneArt?.runtimes).toEqual([]);
  });

  it("liefert leere Listen statt geratener Werte bei kaputter Antwort", () => {
    expect(parseAiRuntime(null)).toBeNull();
    const muesig = parseAiRuntime({ runtimes: "keine", stages: 42, runtime_labels: [] });
    expect(muesig?.runtimes).toEqual([]);
    expect(muesig?.stages).toEqual([]);
    expect(muesig?.runtime_labels).toEqual({});
    // Der Container-Host hat einen Default, weil die Meldung sonst leer bliebe.
    expect(muesig?.container_host).toBe(CONTAINER_HOST);
  });

  it("behaelt die Reihenfolge der Wege", () => {
    expect(parseAiRuntime(ANTWORT)?.stages).toHaveLength(3);
    expect(describeStages(parseAiRuntime(ANTWORT)!)).toHaveLength(3);
  });

  it("sagt ehrlich 'keine Vorschlaege', statt eine Stufe zu erfinden", () => {
    expect(describeStages(RUNTIME)).toEqual(["1. Kein KI-Server eingetragen."]);
  });

  it("baut die Auswahl aus den Werten und ihren Beschriftungen", () => {
    const auswahl = runtimeOptions(parseAiRuntime(ANTWORT)!);
    expect(auswahl[0]).toEqual({ value: "auto", label: "Automatisch aus der Adresse" });
    expect(auswahl.map((eintrag) => eintrag.value)).toContain("online");
  });

  it("zeigt ein unbekanntes Label als Wert, damit der Tippfehler sichtbar bleibt", () => {
    const auswahl = runtimeOptions({ ...RUNTIME, runtime_values: ["gibtsnicht"] });
    expect(auswahl[0].label).toBe("gibtsnicht");
  });

  it("bietet mindestens 'auto', auch wenn der Server keine Liste schickt", () => {
    // Ein leeres Auswahlfeld neben einem Pflichtfeld waere eine Frage, die
    // niemand beantworten kann - und `auto` nimmt der Backend immer.
    expect(runtimeOptions(RUNTIME)).toEqual([{ value: "auto", label: "auto" }]);
  });

  it("zeigt einen Port nur, wenn einer dasteht", () => {
    expect(formatRuntimePort(11434)).toBe("11434");
    expect(formatRuntimePort(0)).toBeNull();
  });
});

describe("Ein Preset fuellt das Formular", () => {
  it("schreibt Adresse, Modell und Laufzeit", () => {
    const runtime = parseAiRuntime(ANTWORT)!;
    const preset = presetFor(runtime, "ollama");
    expect(presetFields(preset!)).toEqual({
      base_url: `http://${CONTAINER_HOST}:11434`,
      model: "llama3.2",
      runtime: "ollama",
    });
  });

  it("bietet die native Adresse ausdruecklich an", () => {
    const runtime = parseAiRuntime(ANTWORT)!;
    const felder = presetFields(presetFor(runtime, "lm_studio")!, true);
    expect(felder.base_url).toBe("http://127.0.0.1:1234");
    expect(felder.runtime).toBe("lm_studio");
  });

  it("kennt zu 'auto' kein Preset - das ist keine Laufzeit", () => {
    expect(presetFor(parseAiRuntime(ANTWORT)!, "auto")).toBeNull();
    expect(presetFor(parseAiRuntime(ANTWORT)!, "gibtsnicht")).toBeNull();
  });
});

describe("Host und container", () => {
  it("liest den Host auch ohne Protokoll und mit IPv6-Klammern", () => {
    expect(hostOf("http://localhost:11434")).toBe("localhost");
    expect(hostOf("host.docker.internal:8080")).toBe("host.docker.internal");
    expect(hostOf("http://[::1]:1234")).toBe("::1");
    expect(hostOf("")).toBe("");
    expect(hostOf("::")).toBe("");
  });

  it("erkennt localhost als Container-Falle", () => {
    expect(isContainerLoopback("http://localhost:11434")).toBe(true);
    expect(isContainerLoopback("http://127.0.0.1:1234")).toBe(true);
    expect(isContainerLoopback("http://127.0.0.5:1234")).toBe(true);
    expect(isContainerLoopback(`http://${CONTAINER_HOST}:11434`)).toBe(false);
    expect(isContainerLoopback("https://api.example.com/v1")).toBe(false);
    expect(isContainerLoopback("")).toBe(false);
  });

  it("erkennt lokale Server ueber Loopback, Bruecke, mDNS und private Netze", () => {
    expect(isLocalServer("http://localhost:11434")).toBe(true);
    expect(isLocalServer(`http://${CONTAINER_HOST}:11434`)).toBe(true);
    expect(isLocalServer("http://host.containers.internal:1234")).toBe(true);
    expect(isLocalServer("http://LM-Studio.local:1234/v1")).toBe(true);
    expect(isLocalServer("http://192.168.10.7:11434")).toBe(true);
    expect(isLocalServer("http://10.1.2.3:1234")).toBe(true);
    expect(isLocalServer("http://172.16.5.5:8080")).toBe(true);
    expect(isLocalServer("http://172.32.5.5:8080")).toBe(false);
    expect(isLocalServer("https://api.openai.com/v1")).toBe(false);
    expect(isLocalServer("")).toBe(false);
  });
});

describe("Der host.docker.internal-Hinweis", () => {
  it("ersetzt localhost durch die Container-Adresse", () => {
    expect(dockerHostFor("http://localhost:11434")).toBe(`http://${CONTAINER_HOST}:11434`);
    expect(dockerHostFor("http://127.0.0.1:1234")).toBe(`http://${CONTAINER_HOST}:1234`);
    expect(dockerHostFor("http://localhost:8080/v1")).toBe(`http://${CONTAINER_HOST}:8080`);
  });

  it("fasst eine bereits richtige oder private Adresse nicht an", () => {
    expect(dockerHostFor(`http://${CONTAINER_HOST}:11434`)).toBeNull();
    expect(dockerHostFor("http://192.168.10.7:11434")).toBeNull();
    expect(dockerHostFor("https://api.example.com/v1")).toBeNull();
    // Ohne Port gibt es nichts zu uebertragen.
    expect(dockerHostFor("http://localhost")).toBeNull();
    expect(dockerHostFor("")).toBeNull();
  });

  it("erklaert den Fehler im Formular, mit beiden Adressen", () => {
    const satz = containerTrapNote("http://localhost:11434");
    expect(satz).toContain("Docker");
    expect(satz).toContain(`http://${CONTAINER_HOST}:11434`);
    expect(containerTrapNote(`http://${CONTAINER_HOST}:11434`)).toBeNull();
  });
});

describe("Herkunft einer Antwort", () => {
  it("kennt die drei Stufen und 'none' aus der Dokumentenfrage", () => {
    expect(normalizeSource("local")).toBe("local");
    expect(normalizeSource("provider")).toBe("provider");
    expect(normalizeSource("fallback")).toBe("fallback");
    expect(normalizeSource("none")).toBe("none");
  });

  it("liest den alten Wert 'ai' als Online-Provider", () => {
    // Ein Cacheeintrag von gestern darf nicht als Datenluege erscheinen.
    expect(normalizeSource("ai")).toBe("provider");
  });

  it("behandelt Unbekanntes als Daten-Satz, nicht als lokal", () => {
    // Eine erfundene *lokale* Herkunft waere die schlimmere Luege.
    expect(normalizeSource("etwas")).toBe("fallback");
    expect(normalizeSource(undefined)).toBe("fallback");
    expect(normalizeSource(42)).toBe("fallback");
  });

  it("unterscheidet im Badge, ob ein Modell geantwortet hat", () => {
    expect(describeSource("local", "qwen")).toEqual({ text: "Lokales Modell · qwen", fromModel: true });
    expect(describeSource("provider", "gpt-4o-mini")).toEqual({ text: "KI-Antwort · gpt-4o-mini", fromModel: true });
    expect(describeSource("fallback")).toEqual({ text: "Aus deinen Daten", fromModel: false });
    expect(describeSource("none")).toEqual({ text: "Keine Treffer", fromModel: false });
  });

  it("nennt den Namen des Modells nur, wenn es eines gibt", () => {
    expect(describeSource("local").text).toBe("Lokales Modell");
    expect(describeSource("local", null).text).toBe("Lokales Modell");
    expect(describeSource("local", "").text).toBe("Lokales Modell");
  });
});
