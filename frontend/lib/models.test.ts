import { describe, expect, it } from "vitest";
import {
  canCancelModelDownload,
  canDeleteModel,
  canStartModelDownload,
  describeModelStatus,
  describeModelTask,
  formatModelBytes,
  modelFitsFreeSpace,
  modelProgress,
  parseModelCatalog,
  type ModelStatusPayload,
} from "./models";

const KATALOG_ANTWORT = {
  models: [
    {
      id: "qwen2.5-1.5b-instruct-q4km",
      label: "Qwen2.5 1.5B Instruct (Q4_K_M)",
      task: "text_generation",
      family: "GGUF für llama.cpp",
      description: "Mehrsprachiges Anweisungsmodell.",
      license: "Apache-2.0",
      source_repository: "bartowski/Qwen2.5-1.5B-Instruct-GGUF",
      source_page: "https://huggingface.co/bartowski/Qwen2.5-1.5B-Instruct-GGUF",
      parameter_count: 1543714304,
      quantization: "Q4_K_M",
      context_length: 32768,
      size_bytes: 986048768,
      ram_estimate_bytes: 1900000000,
      ram_estimate_gib: 1.8,
      ram_is_estimate: true,
      ram_basis: "1,54 Milliarden Parameter mit 4,8 Bit pro Gewicht (Q4_K_M)",
      files: [{ name: "Qwen.gguf", size_bytes: 986048768, sha256: "a".repeat(64) }],
      status: {
        status: "downloading",
        bytes_downloaded: 500000000,
        bytes_total: 986048768,
        progress_percent: 50.7,
        speed_bytes_per_second: 1048576,
        current_file: "Qwen.gguf",
        verified: false,
        message: "Wird geladen.",
      },
    },
  ],
  storage: { available: true, writable: true, free_bytes: 10_000_000_000, message: "Ablage ok." },
  shared_installation: true,
};

function status(overrides: Partial<ModelStatusPayload> = {}): ModelStatusPayload {
  return {
    status: "not_downloaded",
    bytes_downloaded: 0,
    bytes_total: 1000,
    progress_percent: 0,
    speed_bytes_per_second: null,
    current_file: null,
    verified: false,
    message: "",
    ...overrides,
  };
}

describe("parseModelCatalog", () => {
  it("engt eine vollständige Antwort auf die angezeigten Felder", () => {
    const katalog = parseModelCatalog(KATALOG_ANTWORT);
    expect(katalog).not.toBeNull();
    expect(katalog?.models).toHaveLength(1);
    const modell = katalog?.models[0];
    expect(modell?.id).toBe("qwen2.5-1.5b-instruct-q4km");
    expect(modell?.size_bytes).toBe(986048768);
    expect(modell?.ram_is_estimate).toBe(true);
    expect(modell?.status.status).toBe("downloading");
    expect(modell?.status.current_file).toBe("Qwen.gguf");
    expect(katalog?.storage.writable).toBe(true);
  });

  it("verwirft kaputte Antworten, statt sie zu raten", () => {
    expect(parseModelCatalog(null)).toBeNull();
    expect(parseModelCatalog("kein JSON")).toBeNull();
    expect(parseModelCatalog([])).toBeNull();
  });

  it("überspringt einen Eintrag ohne id, statt einen Knopf ohne Ziel zu bauen", () => {
    const katalog = parseModelCatalog({ ...KATALOG_ANTWORT, models: [{ label: "ohne id" }] });
    expect(katalog?.models).toEqual([]);
  });

  it("füllt fehlende Zahlen mit 0 und fehlende Werte mit 'unbekannt'", () => {
    const katalog = parseModelCatalog({ models: [{ id: "klein" }] });
    expect(katalog?.models[0].label).toBe("klein");
    expect(katalog?.models[0].family).toBe("unbekannt");
    expect(katalog?.models[0].size_bytes).toBe(0);
    expect(katalog?.models[0].parameter_count).toBeNull();
    expect(katalog?.storage.free_bytes).toBeNull();
  });
});

describe("formatModelBytes", () => {
  it("schreibt deutsche Einheiten mit Komma", () => {
    expect(formatModelBytes(986048768)).toBe("940,4 MiB");
    expect(formatModelBytes(3 * 1024 * 1024 * 1024)).toBe("3,0 GiB");
    expect(formatModelBytes(512)).toBe("512 Byte");
  });

  it("sagt 'unbekannt' statt einer erfundenen Zahl", () => {
    expect(formatModelBytes(0)).toBe("unbekannt");
    expect(formatModelBytes(null)).toBe("unbekannt");
    expect(formatModelBytes(undefined)).toBe("unbekannt");
    expect(formatModelBytes(Number.NaN)).toBe("unbekannt");
  });
});

describe("describeModelStatus", () => {
  it("übersetzt jeden Zustand des Backends", () => {
    expect(describeModelStatus("installed")).toBe("Geladen");
    expect(describeModelStatus("downloading")).toBe("Wird geladen");
    expect(describeModelStatus("interrupted")).toBe("Unterbrochen");
    expect(describeModelStatus("failed")).toBe("Fehlgeschlagen");
    expect(describeModelStatus("not_downloaded")).toBe("Nicht geladen");
    expect(describeModelStatus("irgendwas Neues")).toBe("Nicht geladen");
  });
});

describe("modelProgress", () => {
  it("rechnet den Anteil aus den Bytes, nicht aus dem gelieferten Prozentwert", () => {
    expect(modelProgress({ bytes_downloaded: 250, bytes_total: 1000 })).toBe(25);
    // Der gelieferte Prozentwert kann falsch sein - die Bytes nicht.
    expect(modelProgress({ bytes_downloaded: 250, bytes_total: 1000 })).not.toBe(999);
  });

  it("bleibt zwischen 0 und 100", () => {
    expect(modelProgress({ bytes_downloaded: 5000, bytes_total: 1000 })).toBe(100);
    expect(modelProgress({ bytes_downloaded: -5, bytes_total: 1000 })).toBe(0);
    expect(modelProgress({ bytes_downloaded: 10, bytes_total: 0 })).toBe(0);
  });
});

describe("die Knöpfe richten sich nach dem Zustand", () => {
  it("ein laufender Download lässt sich weder neu starten noch abbrechen-weg", () => {
    const laeuft = status({ status: "downloading", bytes_downloaded: 10, bytes_total: 100 });
    expect(canStartModelDownload(laeuft)).toBe(false);
    expect(canCancelModelDownload(laeuft)).toBe(true);
    expect(canDeleteModel(laeuft)).toBe(false);
  });

  it("ein Teilstand lässt sich fortsetzen und löschen", () => {
    const teilstand = status({ status: "interrupted", bytes_downloaded: 40, bytes_total: 100 });
    expect(canStartModelDownload(teilstand)).toBe(true);
    expect(canCancelModelDownload(teilstand)).toBe(false);
    expect(canDeleteModel(teilstand)).toBe(true);
  });

  it("ohne Dateien gibt es nichts zu löschen", () => {
    expect(canDeleteModel(status())).toBe(false);
    expect(canDeleteModel(status({ status: "installed" }))).toBe(true);
  });
});

describe("describeModelTask", () => {
  it("übersetzt die Aufgabe und behält einen unbekannten Wert", () => {
    expect(describeModelTask("text_generation")).toBe("Text");
    expect(describeModelTask("speech_to_text")).toBe("Sprache zu Text");
    expect(describeModelTask("text_to_speech")).toBe("Text zu Sprache");
    expect(describeModelTask("embedding")).toBe("Einbettungen");
    expect(describeModelTask("future_task")).toBe("future_task");
  });
});

describe("modelFitsFreeSpace", () => {
  it("vergleicht mit dem freien Speicher, solange er bekannt ist", () => {
    expect(modelFitsFreeSpace(100, 200)).toBe(true);
    expect(modelFitsFreeSpace(300, 200)).toBe(false);
  });

  it("bleibt unentschieden, statt zu raten", () => {
    expect(modelFitsFreeSpace(100, null)).toBeNull();
    expect(modelFitsFreeSpace(100, 0)).toBeNull();
  });
});
