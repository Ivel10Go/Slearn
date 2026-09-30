// Baseline of the deviations that are accepted on purpose.
//
// The repository is not green at HEAD, and that is documented rather than
// accidental. `lib/types.ts:8` states that the Zod schemas are
// `z.object({}).passthrough()` stubs which validate nothing, so the type layer
// is a deliberate workaround. Two frontend commands are red because of it:
//
//   npm test        1 failed | 85 passed   (lib/types.test.ts)
//   npm run lint   22 errors              (@typescript-eslint/no-explicit-any)
//
// This file is NOT "lint is off". It names the single failing test and the
// exact (file, rule) pairs, plus a per-file ceiling on how many findings those
// pairs may produce. Everything else fails the job:
//
//   * a new failing test                     -> fails
//   * a lint error in any other file/rule    -> fails
//   * one more `any` in an allowlisted file  -> fails (the ceiling is not a
//                                               rubber stamp)
//   * a known failure that disappeared       -> passes, but warns, because a
//                                               stale entry is a hole that can
//                                               hide the next regression
//
// Keep the entries as narrow as the real output allows. "Ignore frontend lint"
// would have been a one-liner and would have made the pipeline worthless.

/** @typedef {{ tool: string, workingDirectory: string, failingTests: string[] }} TestBaseline */
/** @typedef {{ tool: string, workingDirectory: string, rules: Record<string, Record<string, number>> }} LintBaseline */

/** @type {Record<string, TestBaseline | LintBaseline>} */
export const knownFailures = {
  "frontend-test": {
    // `npm test` in frontend/ -- vitest run, see frontend/vitest.config.mts.
    // Entry format: "<repo-relative file> > <describe> > <it>", built by
    // check-known-failures.mjs from ancestorTitles + title.
    tool: "npm test",
    workingDirectory: "frontend",
    failingTests: [
      "lib/types.test.ts > Dashboard-Validierung > lehnt fehlerhafte API-Daten ab",
    ],
  },
  "frontend-lint": {
    // `npm run lint` in frontend/ -- eslint . -f json.
    // Key: repo-relative file. Value: allowed ruleId -> maximum finding count.
    // 7 + 15 = the 22 errors that `npm run lint` reports at HEAD.
    tool: "npm run lint",
    workingDirectory: "frontend",
    rules: {
      "lib/schemas.ts": { "@typescript-eslint/no-explicit-any": 7 },
      "lib/types.ts": { "@typescript-eslint/no-explicit-any": 15 },
    },
  },
};

export function baselineFor(name) {
  const baseline = knownFailures[name];
  if (!baseline) {
    throw new Error(
      `No baseline registered for "${name}". Known keys: ${Object.keys(knownFailures).join(", ")}`,
    );
  }
  return baseline;
}
