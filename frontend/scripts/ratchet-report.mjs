// The JSON form the ratchet snapshot collector reads, shared by the three
// ratchets under frontend/scripts.
//
//   node scripts/import-boundary-ratchet.mjs --json
//
// prints ONE JSON object on stdout and nothing else. The human report is not
// printed in this mode on purpose: the collector parses stdout, and a report
// interleaved with it is not a report. Errors keep going to stderr, where they
// were already going.
//
// The exit code stays the authority on the verdict and --json does not change
// it: 0 pass, 1 violation, 2 could not judge. The JSON carries the counts the
// exit code cannot. A collector that trusted the JSON alone would read a
// checker that crashed before printing anything as "no data", which is the one
// answer this whole arrangement exists to avoid.
import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'

/** Fewer violations is better, for every ratchet that reports through here. */
export const BETTER = 'down'

/**
 * Whether the caller asked for the machine-readable form. Read from argv here
 * so that the default run stays byte-for-byte what it was: a checker whose
 * human output grew a JSON line would be a change to every CI log that greps it.
 */
export const asJson = process.argv.includes('--json')

/** Write the record. Only ever in --json mode; throwing otherwise keeps it that way. */
export function emit(record) {
  if (!asJson) throw new Error('ratchet-report: emit() is only for --json runs')
  process.stdout.write(`${JSON.stringify(record)}\n`)
}

/**
 * "Could not judge" in the same JSON form, and exit 2 — the code the snapshot
 * records as `cannot_judge`. Never exit 0 from here: a checker that could not
 * run has not judged a clean tree, and saying otherwise is how a gate goes
 * green over files it never read.
 *
 * A default run prints nothing new on stdout: without --json there is no
 * record to write and only the exit code carries the verdict, as before.
 *
 * @param {{id: string, better?: string}} base the record's stable fields
 * @param {string} reason the checker's own error text, trimmed for the file
 */
export function cannotJudge({ id, better = BETTER }, reason) {
  if (asJson) emit({ id, better, status: 'cannot_judge', reason: String(reason).trim().slice(0, 2000) })
  process.exit(2)
}

/**
 * The record for a run that judged its tree. `result` is what `compare()`
 * returns.
 *
 * The fields here are the ones the checker is the only witness to. `area` and
 * `rule_fingerprint` are not among them: the collector that knows which rule
 * files belong to which check adds those, so that a rule file list lives in one
 * place instead of seven. See docs/topics/棘轮页方案, section 3.1.
 *
 * `stale` is every baseline entry the run no longer matches — the frozen
 * allowance is still on the books and no longer needed. `details` is the
 * per-file breakdown, capped: the page expands it, and an unbounded list would
 * make one committed snapshot the size of the tree.
 *
 * @param {{id: string, better?: string, result: any, details?: unknown[]}} input
 */
export function verdict({ id, better = BETTER, result, details = [] }) {
  return {
    id,
    better,
    status: result.ok ? 'pass' : 'fail',
    actual: result.currentTotal,
    frozen: result.baselineTotal,
    stale: result.improvements.map(({ file, base, now }) => ({ file, frozen: base, actual: now })),
    details: details.slice(0, 200),
  }
}

/**
 * SHA-256 over the bytes of the files that decide a check, in the order given.
 * The path is mixed in with the content so that renaming a rule file changes
 * the fingerprint too — a rule that moved is a rule whose provenance changed,
 * even when its text did not.
 *
 * The BASELINE is deliberately not part of this: it moves every time somebody
 * pays debt down, and a fingerprint that moved with it would mark every
 * improvement as a change of rules. See docs/topics/棘轮页方案 for the split.
 *
 * @param {{root: string, files: string[]}} input
 * @returns {string} `sha256:<hex>` over an unreadable file's path only
 */
export function fingerprint({ root, files }) {
  const hash = createHash('sha256')
  for (const file of files) {
    hash.update(file)
    hash.update('\0')
    try {
      hash.update(readFileSync(`${root}/${file}`))
    } catch {
      // A rule file that is not there is part of the fingerprint: it makes the
      // hash of a tree that never had it differ from one whose file was read.
      hash.update('\0missing')
    }
    hash.update('\0')
  }
  return `sha256:${hash.digest('hex')}`
}
