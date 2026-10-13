// Pure logic for the request-path gate: every path this frontend hands to
// `request()` has to be a path the backend actually serves. No I/O, no process —
// see check-request-paths-core.test.mjs, run by `node --test` in CI.
//
// WHY THIS EXISTS: the frontend's view of the API is hand-written. `cx_types.ts`
// declares the shapes it expects, `src/api/*.ts` spell the paths out as string
// literals, and nothing connects either half to the backend — so a route that is
// renamed, moved under a new prefix or never existed type-checks, builds, and
// fails at runtime in the browser. The backend already publishes the authority
// for this: `backend/scripts/route_index.json` is every route the app serves,
// kept current by the contract test that reads it
// (backend/tests/contract/test_api_addressing_contract.py).
//
// WHAT IT JUDGES, AND WHAT IT THEREFORE DOES NOT: only the paths it can read off
// the source. `request(...)` whose first argument is a string literal, or a
// template whose `${…}` holes are whole segments (`/topics/${id}/reopen`), is
// judged against the index. A path assembled some other way — a hole glued into
// a segment (`` `${qs ? '?a=1' : ''}` ``), a leading `/topics/${taskPath(id)}`
// prefix helper, a plain variable — is *not* judged, and the report counts those
// separately rather than letting them read as checked. A gate that says "clean"
// because it understood nothing is worse than no gate.
//
// The method is not judged: `request(path, { method: 'POST' })` defaults to GET,
// takes the verb from a variable often enough, and a wrong verb on the right
// path is a different bug from a path that goes nowhere.

import ts from 'typescript'

/**
 * The helpers that take a path as their first argument, and the prefix each one
 * puts in front of it. The index holds the paths the app itself routes on, so
 * the `/api` that `request` fetches under is not part of it, while the connector
 * is a mount of its own and keeps its prefix.
 *
 * Only bare identifiers: `instance.request(config)` (axios) and
 * `navigator.locks.request(name, …)` take something that is not a path, and
 * matching on the property name would drag them in.
 */
const PATH_CALLS = new Map([
  ['request', ''],
  ['readSince', ''],
  ['legacyRequest', ''],
  ['connectorRequest', '/connector'],
])

/** Paths the gate never looks at. */
export function shouldScan(relPath) {
  const p = String(relPath).replaceAll('\\', '/')
  if (!/\.(ts|vue)$/.test(p)) return false
  if (/(^|\/)__tests__\//.test(p)) return false
  if (/\.(spec|test)\.ts$/.test(p)) return false
  return true
}

/** Stands in for a route parameter or an interpolation: matches one segment. */
const ANY = '\u0000'

/** `'topics'`, `'*'`, `'reopen'` — what a path is made of, one segment each. */
export function segmentList(path) {
  const staticPath = String(path).split('?')[0].split('#')[0]
  const body = staticPath.startsWith('/') ? staticPath.slice(1) : staticPath
  if (body === '') return []
  return body.split('/').map((segment) => (segment.startsWith('{') && segment.endsWith('}') ? ANY : segment))
}

/**
 * The segments of one string-literal argument, or `null` when the path is built
 * in a way this gate cannot read (see the header).
 * @param {string} raw the literal's text, `${…}` written as `` `${}` ``
 */
export function shapeSegments(raw) {
  const text = String(raw)
  // A path is read only when it starts at the root: `${base(projectId)}/x` is
  // built from a helper's return value, and what that is is not here.
  if (!text.startsWith('/')) return null
  if (!text.includes('${')) return segmentList(text)
  // An interpolation is judged only where it is a whole segment: `/topics/${…}`
  // is one, `transcript${…}` is not — the second is how a query string or a
  // suffix gets appended, and what it produces cannot be read off the source.
  if (/[^/]\$\{\}|\$\{\}[^/]/.test(text)) return null
  return segmentList(text.replace(/\$\{\}/g, ANY))
}

/** Every `request('…')`-shaped call in one file. @returns {Array<{line:number, raw:string, segments:string[]|null}>} */
export function requestCalls(relPath, text) {
  const source = ts.createSourceFile(String(relPath), String(text), ts.ScriptTarget.Latest, true)
  const lineOf = (offset) => source.getLineAndCharacterOfPosition(offset).line + 1
  const calls = []
  const visit = (node) => {
    const prefix = ts.isCallExpression(node) ? pathPrefix(node.expression) : undefined
    if (prefix !== undefined) {
      const raw = literalText(node.arguments[0])
      const path = raw === null ? null : prefix + raw
      calls.push({
        line: lineOf(node.getStart(source)),
        raw: path,
        segments: path === null ? null : shapeSegments(path),
      })
    }
    ts.forEachChild(node, visit)
  }
  visit(source)
  return calls
}

/** The prefix of the helper being called, or `undefined` when it takes no path. */
function pathPrefix(expression) {
  if (!ts.isIdentifier(expression)) return undefined
  return PATH_CALLS.get(expression.text)
}

/**
 * The text of a string-literal argument, with each `${…}` written as `` `${}` ``
 * so the caller can look at the shape without the expression inside it. `null`
 * for anything else — a variable, a concatenation, a call.
 */
function literalText(node) {
  if (!node) return null
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) return node.text
  if (!ts.isTemplateExpression(node)) return null
  let text = node.head.text
  for (const span of node.templateSpans) text += '${}' + span.literal.text
  return text
}

/**
 * The `<script>` blocks of a `.vue` file, each with the line its text starts on.
 *
 * A block's tags open and close at column 0; an indented `</script>` is inside
 * a string or a comment. The `i18n` gate reads SFC blocks the same way, for the
 * same reason: a `.vue` is not one script, and handing the whole file to the
 * parser would read the template as code — where a `request('/x')` written into
 * prose or an example is not a call this gate should report. Only the script is
 * judged; a component that calls the network layer at all is already a
 * boundary violation (eslint.boundary.config.mjs).
 */
function scriptBlocks(text) {
  const src = String(text)
  const blocks = []
  const opener = /^<script(?=[\s>])/gm
  const closer = /^<\/script>/gm
  for (let open = opener.exec(src); open; open = opener.exec(src)) {
    const openEnd = src.indexOf('>', open.index)
    if (openEnd === -1) break
    closer.lastIndex = openEnd
    const close = closer.exec(src)
    if (!close) break
    let start = openEnd + 1
    if (src[start] === '\n') start++
    blocks.push({ text: src.slice(start, close.index), lineOffset: countLines(src.slice(0, start)) })
    opener.lastIndex = close.index
  }
  return blocks
}

function countLines(text) {
  let lines = 0
  for (const char of String(text)) if (char === '\n') lines++
  return lines
}

/** `{path: '…', methods: […], …}` records → the route path segments, once each. */
export function routeShapes(records) {
  const shapes = new Map()
  for (const record of records) {
    const path = record && record.path
    if (typeof path !== 'string') continue
    const segments = segmentList(path)
    shapes.set(segments.join('/'), segments)
  }
  return [...shapes.values()]
}

function sameShape(frontend, route) {
  if (frontend.length !== route.length) return false
  return route.every((segment, i) => segment === ANY || frontend[i] === ANY || segment === frontend[i])
}

/**
 * @param {Array<{path: string, text: string}>} files
 * @param {Array<object>} records route_index.json lines
 * @returns {{checked: number, unjudged: number, offenders: Array<{file: string, line: number, raw: string}>}}
 */
export function findOffenders(files, records) {
  const routes = routeShapes(records)
  const offenders = []
  let checked = 0
  let unjudged = 0
  for (const { path, text } of files) {
    const rel = String(path).replaceAll('\\', '/')
    if (!shouldScan(rel)) continue
    const parts = rel.endsWith('.vue') ? scriptBlocks(text) : [{ text, lineOffset: 0 }]
    for (const part of parts) {
      for (const { line, raw, segments } of requestCalls(rel, part.text)) {
        if (segments === null) {
          unjudged++
          continue
        }
        checked++
        const at = line + part.lineOffset
        if (!routes.some((route) => sameShape(segments, route))) offenders.push({ file: rel, line: at, raw })
      }
    }
  }
  offenders.sort((a, b) => a.file.localeCompare(b.file) || a.line - b.line)
  return { checked, unjudged, offenders }
}

/**
 * What the gate prints. A failure names the backend file that must gain the
 * route, because "this path goes nowhere" is only actionable with the answer to
 * "so where does it go".
 * @param {ReturnType<typeof findOffenders>} result
 */
export function formatReport(result) {
  const { checked, unjudged, offenders } = result
  if (offenders.length === 0) {
    return [
      `Request paths match backend/scripts/route_index.json (${checked} call site(s) judged,`,
      `  ${unjudged} skipped: path built from a variable or a helper).`,
    ].join('\n')
  }
  const lines = ['Request paths the backend does not serve:']
  for (const { file, line, raw } of offenders) lines.push(`  ${file}:${line}  ${raw}`)
  lines.push('')
  lines.push('A path reaches a route only if some route in the index matches it segment for segment.')
  lines.push('Fix the path, or add the route under backend/app/api/routes/ and regenerate the index:')
  lines.push('  cd backend && uv run python -m scripts.route_index --emit > scripts/route_index.json')
  lines.push(`${offenders.length} path(s) of ${checked} judged (${unjudged} skipped).`)
  return lines.join('\n')
}
