// The JSON form ci:fast reads, shared by the checks
// under frontend/scripts — the ratchets and the plain checks alike.
//
//   node scripts/import-boundary-ratchet.mjs --json
//
// prints ONE JSON object on stdout and nothing else. The human report is not
// printed in this mode on purpose: ci:fast parses stdout, and a report
// interleaved with it is not a report. Errors keep going to stderr, where they
// were already going.
//
// The exit code stays the authority on the verdict and --json does not change
// it: 0 pass, 1 violation, 2 could not judge. The JSON carries the counts the
// exit code cannot. A collector that trusted the JSON alone would read a
// checker that crashed before printing anything as "no data", which is the one
// answer this whole arrangement exists to avoid.
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
 * "Could not judge" in the same JSON form, and exit 2. Never exit 0 from here: a checker that could not
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
 * The fields here are the ones the checker is the only witness to.
 *
 * `stale` is every baseline entry the run no longer matches — the frozen
 * allowance is still on the books and no longer needed. `details` is the
 * per-file breakdown, capped so one record does not grow with the entire tree.
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
 * The `result` a plain check hands `verdict()`: nothing is frozen, so every
 * violation it counted is a failure and there is no allowance to go stale.
 * @param {Record<string, number>} counts violations per file
 */
export function plainResult(counts) {
  const currentTotal = Object.values(counts).reduce((a, b) => a + b, 0)
  return { ok: currentTotal === 0, currentTotal, baselineTotal: 0, improvements: [] }
}
