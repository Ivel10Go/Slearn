import { appendFile, mkdir, readFile } from "node:fs/promises";
import path from "node:path";
import type { WaitlistEntry } from "./waitlist";

/*
 * Wohin die Warteliste schreibt.
 *
 * - **Supabase**, wenn `WAITLIST_SUPABASE_URL` und
 *   `WAITLIST_SUPABASE_SERVICE_ROLE_KEY` gesetzt sind. Die Tabelle legt
 *   `supabase/waitlist.sql` an. Geschrieben wird ueber die REST-Schnittstelle
 *   per `fetch`, ohne zusaetzliches Paket. Der Schluessel bleibt auf dem Server
 *   und bekommt nie ein `NEXT_PUBLIC_`-Praefix.
 * - **Sonst eine JSONL-Datei** (`WAITLIST_FILE`, Standard `.data/waitlist.jsonl`).
 *   Das reicht fuer die Entwicklung und fuer einen eigenen Server mit
 *   beschreibbarem Laufwerk. Auf Serverless-Hostern ist das Laufwerk fluechtig
 *   oder schreibgeschuetzt - dort Supabase verwenden.
 *
 * Eine doppelte Adresse ist in beiden Faellen kein Fehler, sondern ein No-Op.
 */

export type StoreResult = "created" | "duplicate";

export class WaitlistStoreError extends Error {}

export async function saveWaitlistEntry(entry: WaitlistEntry, source: string | null): Promise<StoreResult> {
  const url = process.env.WAITLIST_SUPABASE_URL?.trim();
  const key = process.env.WAITLIST_SUPABASE_SERVICE_ROLE_KEY?.trim();
  if (url && key) {
    return saveToSupabase(url, key, entry, source);
  }
  return saveToFile(entry, source);
}

async function saveToSupabase(baseUrl: string, key: string, entry: WaitlistEntry, source: string | null) {
  const endpoint = `${baseUrl.replace(/\/+$/, "")}/rest/v1/waitlist?on_conflict=email`;
  let response: Response;
  try {
    response = await fetch(endpoint, {
      method: "POST",
      headers: {
        apikey: key,
        Authorization: `Bearer ${key}`,
        "Content-Type": "application/json",
        // Bei einer vorhandenen Adresse nichts aendern und keine Zeile zurueckgeben.
        Prefer: "resolution=ignore-duplicates,return=representation",
      },
      body: JSON.stringify({ ...entry, source }),
      signal: AbortSignal.timeout(8000),
      cache: "no-store",
    });
  } catch (error) {
    throw new WaitlistStoreError(`Supabase nicht erreichbar: ${(error as Error).message}`);
  }
  if (!response.ok) {
    throw new WaitlistStoreError(`Supabase antwortete mit ${response.status}: ${await response.text()}`);
  }
  const rows = (await response.json().catch(() => [])) as unknown[];
  return Array.isArray(rows) && rows.length > 0 ? "created" : "duplicate";
}

// Ein Schreiber nach dem anderen, damit zwei gleichzeitige Anfragen mit
// derselben Adresse nicht beide "neu" sehen.
let queue: Promise<unknown> = Promise.resolve();

function saveToFile(entry: WaitlistEntry, source: string | null): Promise<StoreResult> {
  const run = queue.then(async () => {
    const file = path.resolve(process.env.WAITLIST_FILE?.trim() || path.join(process.cwd(), ".data", "waitlist.jsonl"));
    try {
      await mkdir(path.dirname(file), { recursive: true });
      const existing = await readFile(file, "utf8").catch((error: NodeJS.ErrnoException) => {
        if (error.code === "ENOENT") {
          return "";
        }
        throw error;
      });
      const known = existing.split("\n").some((line) => {
        if (!line.trim()) {
          return false;
        }
        try {
          return (JSON.parse(line) as { email?: string }).email === entry.email;
        } catch {
          return false;
        }
      });
      if (known) {
        return "duplicate" as const;
      }
      const row = { ...entry, source, created_at: new Date().toISOString() };
      await appendFile(file, `${JSON.stringify(row)}\n`, "utf8");
      return "created" as const;
    } catch (error) {
      throw new WaitlistStoreError(`Warteliste nicht beschreibbar (${file}): ${(error as Error).message}`);
    }
  });
  queue = run.catch(() => undefined);
  return run;
}
