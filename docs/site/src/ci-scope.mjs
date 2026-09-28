// Which suites a merge diff selects, in the language the page and the build
// both speak.
//
// This is a second implementation of `select()` in
// `.github/scripts/required-ci.py`, and a second implementation is a second
// thing to drift. So build.mjs runs a set of sample path lists through the real
// Python function and through this one, and fails the build if any answer
// differs — the data (which patterns, which suites) it reads from
// `.github/scripts/required-ci-paths.json` itself.

// Changes to the gate itself exercise every callable suite. Spelled the way
// `select()` spells it, including the `startswith` covering required-ci.py,
// required-ci-paths.json and any later sibling with that prefix.
const GATE_WORKFLOW = '.github/workflows/required-ci.yml'
const GATE_SCRIPT_PREFIX = '.github/scripts/required-ci'
const GATE_TEST = '.github/scripts/test_required_ci.py'
const ALWAYS = ['guards']

export function isGate(path) {
  return path === GATE_WORKFLOW || path.startsWith(GATE_SCRIPT_PREFIX) || path === GATE_TEST
}

const escapeRe = (c) => (/[.*+?^${}()|[\]\\]/.test(c) ? `\\${c}` : c)

// `fnmatch.translate`, as CPython writes it: `*` becomes `.*` and therefore
// crosses `/` — `backend/**` has to cover `backend/app/api/x.py`. `?` is one
// character, `[seq]` / `[!seq]` are character classes, everything else is
// literal. A JS regex cannot say `\Z`, so the caller compares the whole match.
function translate(pattern) {
  let out = ''
  let i = 0
  const n = pattern.length
  while (i < n) {
    const c = pattern[i++]
    if (c === '*') { out += '.*'; continue }
    if (c === '?') { out += '.'; continue }
    if (c === '[') {
      let j = i
      if (j < n && pattern[j] === '!') j++
      if (j < n && pattern[j] === ']') j++
      while (j < n && pattern[j] !== ']') j++
      if (j >= n) out += '\\['
      else {
        let stuff = pattern.slice(i, j).replace(/\\/g, '\\\\')
        i = j + 1
        stuff = stuff[0] === '!' ? `^${stuff.slice(1)}` : stuff[0] === '^' ? `\\${stuff}` : stuff
        out += `[${stuff}]`
      }
      continue
    }
    out += escapeRe(c)
  }
  return out
}

const cache = new Map()
export function fnmatchcase(name, pattern) {
  let re = cache.get(pattern)
  if (!re) { re = new RegExp(`^(?:${translate(pattern)})$`, 's'); cache.set(pattern, re) }
  const m = re.exec(name)
  // `$` is happy to match before a trailing newline; Python's `\Z` is not.
  return !!m && m[0] === name
}

// The pattern a path matched, or `null`. `why` says which of the three rules
// decided it, so the page can explain the answer instead of just showing it.
export function selectSuites(paths, suites) {
  const gate = paths.some(isGate)
  const out = {}
  for (const [suite, patterns] of Object.entries(suites)) {
    const hits = []
    if (!gate && !ALWAYS.includes(suite)) {
      for (const path of paths) {
        for (const pattern of patterns) {
          if (fnmatchcase(path, pattern) && !hits.some((h) => h[0] === path && h[1] === pattern)) hits.push([path, pattern])
        }
      }
    }
    out[suite] = {
      run: gate || ALWAYS.includes(suite) || hits.length > 0,
      hits,
      why: gate ? 'gate' : ALWAYS.includes(suite) ? 'always' : hits.length ? 'match' : '',
    }
  }
  return out
}

export const ALWAYS_SUITES = ALWAYS
