// Fuehrt die latest-mac.yml der beiden Mac-Builds zusammen.
//
// Jeder Mac-Job kennt nur seine Architektur. electron-updater liest eine
// einzige latest-mac.yml und waehlt daraus die Dateien, deren Name zur
// Architektur passt (arm64 im Namen oder nicht). Die erste Datei bleibt
// vollstaendig (version, path, sha512, releaseDate), die `files:`-Eintraege
// der zweiten werden angehaengt. Kein YAML-Paket: der Runner hat keins, und
// das Format von electron-builder ist fest.
import { readFileSync } from "node:fs";

const [first, second] = process.argv.slice(2);
if (!first || !second) {
  console.error("Aufruf: merge-latest-mac.mjs <latest-mac.yml> <latest-mac.yml>");
  process.exit(2);
}

function filesBlock(lines) {
  const start = lines.indexOf("files:");
  if (start < 0) throw new Error("kein files:-Block");
  let end = start + 1;
  while (end < lines.length && lines[end].startsWith("  ")) end += 1;
  return { start, end };
}

const read = (path) => readFileSync(path, "utf8").replace(/\r\n/g, "\n").split("\n");
const a = read(first);
const b = read(second);
const version = (lines) => lines.find((line) => line.startsWith("version:"));
if (version(a) !== version(b)) {
  throw new Error(`Versionen passen nicht: ${version(a)} / ${version(b)}`);
}

const blockA = filesBlock(a);
const blockB = filesBlock(b);
const merged = [...a.slice(0, blockA.end), ...b.slice(blockB.start + 1, blockB.end), ...a.slice(blockA.end)];
process.stdout.write(merged.join("\n"));
