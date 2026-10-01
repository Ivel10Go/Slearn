// Friert das Backend mit PyInstaller ein: backend/ -> desktop/resources/backend.
//
// Gebaut wird in einem eigenen venv (`desktop/.venv-backend`) mit genau den
// Abhaengigkeiten aus `backend/requirements.txt`. Mit dem globalen Python
// sammelte PyInstaller alles ein, was dort zufaellig installiert ist - beim
// ersten Versuch bis hin zu scikit-learn -, und der Installer waere um
// Hunderte Megabyte gewachsen.
//
// Das Basis-Python waehlt `LERNRAUM_PYTHON` (sonst `python` bzw. `python3`),
// mindestens 3.12. PyInstaller baut nur fuer das eigene System - den
// Mac-Installer also auf einem Mac.
import { spawnSync } from "node:child_process";
import { existsSync, rmSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const PYINSTALLER = "pyinstaller==6.22.3";

const desktopDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const backendDir = path.resolve(desktopDir, "..", "backend");
const distDir = path.join(desktopDir, "resources");
const workDir = path.join(desktopDir, ".pyinstaller");
const venvDir = path.join(desktopDir, ".venv-backend");
const basePython = process.env.LERNRAUM_PYTHON || (process.platform === "win32" ? "python" : "python3");
const venvPython =
  process.platform === "win32" ? path.join(venvDir, "Scripts", "python.exe") : path.join(venvDir, "bin", "python");

function run(command, args, cwd = desktopDir) {
  const result = spawnSync(command, args, { cwd, stdio: "inherit" });
  if (result.status !== 0) {
    console.error(`Fehlgeschlagen: ${command} ${args.join(" ")}`);
    process.exit(result.status ?? 1);
  }
}

if (!existsSync(venvPython)) {
  run(basePython, ["-m", "venv", venvDir]);
}
run(venvPython, ["-m", "pip", "install", "--quiet", "--upgrade", "pip"]);
run(venvPython, ["-m", "pip", "install", "--quiet", "-r", path.join(backendDir, "requirements.txt"), PYINSTALLER]);

rmSync(path.join(distDir, "backend"), { recursive: true, force: true });
run(
  venvPython,
  ["-m", "PyInstaller", "lernraum-backend.spec", "--noconfirm", "--clean", "--distpath", distDir, "--workpath", workDir],
  backendDir,
);

const executable = path.join(distDir, "backend", process.platform === "win32" ? "lernraum-backend.exe" : "lernraum-backend");
if (!existsSync(executable)) {
  console.error(`Erwartete Datei fehlt: ${executable}`);
  process.exit(1);
}
console.log(`Backend gebaut: ${executable}`);
