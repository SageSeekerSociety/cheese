// `fit_index` and `limit_breach` from `backend/app/domain/memory/files.py`,
// in the language the browser speaks.
//
// The limits themselves are not written here — build.mjs reads them out of that
// file (docs/site/gen/memory_limits.py) and hands them over as data, so the page
// cannot show a 200-line budget the code no longer has. This module only does
// the arithmetic, and build.mjs runs a set of samples through both this port and
// the real Python and fails the build if any answer differs.

const enc = new TextEncoder()

// Python's `str.splitlines()` for text that only holds `\n` — the trailing
// newline does not open an empty last line.
export function splitlines(text) {
  const lines = String(text ?? '').split('\n')
  if (lines.length && lines[lines.length - 1] === '') lines.pop()
  return lines
}

export const syntheticLine = (prefix, lineBytes) => prefix + 'x'.repeat(Math.max(lineBytes - prefix.length, 0))

export function indexTextOf(limits, lines, lineBytes) {
  const line = syntheticLine(limits.linePrefix, lineBytes)
  return `${line}\n`.repeat(lines)
}

// The injection budget: over it, the index is cut at a line boundary and the
// reader is told. Mirrors `files.fit_index` (bytes are UTF-8 bytes there and
// here, which the ASCII synthetic line above keeps equal to characters).
export function fitIndex(limits, text) {
  const lines = splitlines(text)
  const bytes = enc.encode(text).length
  const overBytes = bytes > limits.INDEX_MAX_BYTES
  const overLines = lines.length > limits.INDEX_MAX_LINES
  if (!overBytes && !overLines) return { keptLines: lines.length, keptBytes: bytes, truncated: false, overBytes, overLines, oldLines: lines.length, oldBytes: bytes }
  const kept = []
  let used = 0
  for (const line of lines) {
    const cost = enc.encode(line).length + 1
    if (kept.length >= limits.INDEX_MAX_LINES || used + cost > limits.INDEX_MAX_BYTES) break
    kept.push(line)
    used += cost
  }
  const out = kept.length ? `${kept.join('\n')}\n` : ''
  return { keptLines: kept.length, keptBytes: enc.encode(out).length, truncated: true, overBytes, overLines, oldLines: lines.length, oldBytes: bytes }
}

// One version's single-entry limit. An index line counts only if this version
// added it; a body counts whole. Mirrors `files.limit_breach`.
export function limitBreach(limits, { name, newLineChars, alreadyInIndex, bodyChars, indexName = limits.indexName || 'MEMORY.md' }) {
  if (name === indexName) return alreadyInIndex ? null : newLineChars > limits.INDEX_LINE_MAX ? 'line' : null
  return bodyChars > limits.BODY_MAX ? 'body' : null
}
