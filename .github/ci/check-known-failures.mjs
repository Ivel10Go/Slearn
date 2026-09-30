#!/usr/bin/env node
// Compares a machine-readable tool report against the accepted baseline in
// known-failures.mjs and owns the exit code of the CI step.
//
//   node ../.github/ci/check-known-failures.mjs <frontend-test|frontend-lint> <report.json>
//
// Run it from the directory the tool ran in (frontend/); report paths are
// relativised against the current working directory so that a Windows checkout
// and a Linux runner produce the same keys.
//
// The reports are produced next to the normal console output, never instead of
// it:
//
//   npm test -- --reporter=default --reporter=json --outputFile.json=<file>
//   npm run lint -- --format json --output-file=<file>
//
// Both tools exit non-zero on the accepted baseline, so the workflow swallows
// that exit code with `|| true` and lets this script decide. A missing, empty
// or unreadable report is a hard error: that is exactly the failure a blanket
// `continue-on-error` would hide, because a tool that dies before writing its
// report would otherwise look like "nothing failed".

import { readFileSync } from "node:fs";
import { relative, sep } from "node:path";

import { baselineFor } from "./known-failures.mjs";

const onGitHub = process.env.GITHUB_ACTIONS === "true";
const problems = [];
const warnings = [];

function annotation(kind, message, file, line) {
  const location = file ? ` file=${file}${line ? `,line=${line}` : ""}` : "";
  console.log(`::${kind}${location}::${message}`);
}

function report(kind, message, file, line) {
  problems.push({ file, line, message });
  if (onGitHub) {
    annotation(kind === "error" ? "error" : "warning", message, file, line);
  }
}

function warn(message, file, line) {
  warnings.push({ file, line, message });
  if (onGitHub) {
    annotation("warning", message, file, line);
  }
}

function abort(message) {
  console.error(`Baseline check aborted: ${message}`);
  process.exit(1);
}

function toPosix(value) {
  return value.split(sep).join("/");
}

function relPath(absolutePath) {
  if (typeof absolutePath !== "string" || absolutePath === "") {
    return "<unknown file>";
  }
  return toPosix(relative(process.cwd(), absolutePath)) || absolutePath;
}

function readJson(reportPath) {
  let text;
  try {
    text = readFileSync(reportPath, "utf8");
  } catch (error) {
    abort(
      `no report at "${reportPath}" (${error.code ?? error.message}). ` +
        "The tool most likely crashed before writing it -- that is a failure, not an empty result.",
    );
  }
  try {
    return JSON.parse(text);
  } catch (error) {
    abort(`"${reportPath}" is not valid JSON: ${error.message}`);
  }
}

function checkFrontendTest(report, baseline) {
  const files = report.testResults;
  if (!Array.isArray(files) || !Number.isFinite(report.numTotalTests) || report.numTotalTests === 0) {
    abort(
      "the vitest JSON report contains no test results. An empty suite passes " +
        "vacuously, so this is treated as a failure.",
    );
  }
  const failed = new Map();
  for (const file of files) {
    const fileKey = relPath(file.name);
    for (const assertion of file.assertionResults ?? []) {
      if (assertion.status !== "failed") {
        continue;
      }
      const name = [...(assertion.ancestorTitles ?? []), assertion.title ?? assertion.fullName ?? ""].join(" > ");
      failed.set(`${fileKey} > ${name}`, fileKey);
    }
  }
  const known = new Set(baseline.failingTests);
  for (const [id, file] of failed) {
    if (!known.has(id)) {
      report("error", `unexpected failing test: ${id}`, file);
    }
  }
  for (const id of known) {
    if (!failed.has(id)) {
      warn(`baseline entry is stale, the test passes now: ${id}. Remove it from known-failures.mjs.`);
    }
  }
  console.log(
    `${baseline.tool}: ${report.numPassedTests}/${report.numTotalTests} passed, ` +
      `${failed.size} failed, ${baseline.failingTests.length} of them on the accepted baseline.`,
  );
}

function checkFrontendLint(report, baseline) {
  if (!Array.isArray(report) || report.length === 0) {
    abort(
      "the eslint JSON report lists no files. That means eslint did not lint " +
        "anything, which must not pass as 'clean'.",
    );
  }
  const counts = new Map();
  for (const file of report) {
    const fileKey = relPath(file.filePath);
    for (const message of file.messages ?? []) {
      const rule = message.ruleId ?? "<fatal, no rule id>";
      const key = JSON.stringify([fileKey, rule]);
      const seen = counts.get(key) ?? { file: fileKey, rule, line: message.line, count: 0 };
      seen.count += 1;
      counts.set(key, seen);
    }
  }
  for (const { file, rule, line, count } of counts.values()) {
    const allowed = baseline.rules[file]?.[rule];
    if (allowed === undefined) {
      report("error", `${count}x ${rule} in ${file} is not on the baseline`, file, line);
    } else if (count > allowed) {
      report(
        "error",
        `${file}: ${count}x ${rule}, baseline allows ${allowed}. The exception is a ` +
          "recorded fact, not a budget -- fix the finding or raise the number in known-failures.mjs.",
        file,
        line,
      );
    }
  }
  const total = [...counts.values()].reduce((sum, entry) => sum + entry.count, 0);
  for (const [file, rules] of Object.entries(baseline.rules)) {
    for (const [rule, allowed] of Object.entries(rules)) {
      const seen = counts.get(JSON.stringify([file, rule]))?.count ?? 0;
      if (seen < allowed) {
        warn(`${file}: only ${seen}x ${rule} left, baseline still allows ${allowed}. Lower the number.`);
      }
    }
  }
  console.log(
    `${baseline.tool}: ${report.length} files linted, ${total} findings, ` +
      `${Object.keys(baseline.rules).length} file/rule pairs on the accepted baseline.`,
  );
}

const [name, reportPath] = process.argv.slice(2);
if (!name || !reportPath) {
  abort("usage: check-known-failures.mjs <frontend-test|frontend-lint> <report.json>");
}
const baseline = baselineFor(name);
const parsed = readJson(reportPath);
if (name === "frontend-test") {
  checkFrontendTest(parsed, baseline);
} else if (name === "frontend-lint") {
  checkFrontendLint(parsed, baseline);
} else {
  abort(`no checker implemented for "${name}"`);
}

for (const problem of problems) {
  console.error(`UNEXPECTED ${problem.file}${problem.line ? `:${problem.line}` : ""} ${problem.message}`);
}
for (const warning of warnings) {
  console.warn(`STALE ${warning.message}`);
}
if (problems.length > 0) {
  console.error(`${problems.length} finding(s) outside the accepted baseline. See known-failures.mjs.`);
  process.exit(1);
}
if (warnings.length > 0) {
  console.warn(`${warnings.length} baseline entr(y/ies) need updating. The build still passes.`);
}
console.log("Baseline check passed.");
