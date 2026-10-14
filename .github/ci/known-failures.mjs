// Baseline of the deviations that are accepted on purpose.
//
// Since the dashboard, study and account schemas in frontend/lib/schemas.ts
// became real Zod schemas (October 2026), the baseline is EMPTY: `npm test`
// and `npm run lint` are green at HEAD. The checker stays in place because it
// still catches what a plain exit code hides -- a missing, empty or broken
// report means the tool crashed, not that everything passed.
//
// If a deviation ever has to be accepted again, name it here as narrowly as
// the real output allows:
//
//   * a new failing test                     -> fails unless listed
//   * a lint error in any other file/rule    -> fails unless listed
//   * one more finding than the ceiling      -> fails (the ceiling is not a
//                                               rubber stamp)
//   * a listed failure that disappeared      -> passes, but warns, because a
//                                               stale entry is a hole that can
//                                               hide the next regression

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
    failingTests: [],
  },
  "frontend-lint": {
    // `npm run lint` in frontend/ -- eslint . -f json.
    // Key: repo-relative file. Value: allowed ruleId -> maximum finding count.
    tool: "npm run lint",
    workingDirectory: "frontend",
    rules: {},
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
