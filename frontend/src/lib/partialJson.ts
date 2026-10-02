// A string field read out of a JSON object that is still being written.
//
// A tool call's arguments reach the room as the raw JSON the model has streamed
// so far — `{"reply_to":"…","content":"Hel` — which no JSON parser accepts. What
// the room wants from it is one top-level string value, as far as it goes, and
// whether the model has finished it (`closed`). Everything after the last
// character that can be decoded with certainty is left for the next read: a
// lone `\`, a `\u` short of its four digits, a high surrogate whose low half has
// not arrived yet.

export interface PartialString {
  text: string
  /** The value's closing quote has been written: the text will not grow. */
  closed: boolean
}

const ESCAPES: Record<string, string> = { '"': '"', '\\': '\\', '/': '/', b: '\b', f: '\f', n: '\n', r: '\r', t: '\t' }

interface Scan {
  /** Decoded so far. */
  text: string
  /** Index just past the closing quote, or -1 when the string is unfinished. */
  end: number
}

/** Decode the JSON string whose opening quote is at `start`. */
function scanString(raw: string, start: number): Scan {
  let text = ''
  let i = start + 1
  while (i < raw.length) {
    const ch = raw[i]
    if (ch === '"') return { text, end: i + 1 }
    if (ch !== '\\') {
      text += ch
      i += 1
      continue
    }
    const kind = raw[i + 1]
    if (kind === undefined) break
    if (kind !== 'u') {
      text += ESCAPES[kind] ?? kind
      i += 2
      continue
    }
    const hex = raw.slice(i + 2, i + 6)
    if (!/^[0-9a-fA-F]{4}$/.test(hex)) {
      // Four digits not here yet; anything else is not JSON and ends the read.
      break
    }
    const code = parseInt(hex, 16)
    if (code >= 0xd800 && code <= 0xdbff) {
      // A high surrogate is only half a character: show it with its low half.
      const low = raw.slice(i + 6, i + 12)
      if (!/^\\u[dD][c-fC-F][0-9a-fA-F]{2}$/.test(low)) {
        if (low.length < 6 && /^(\\(u([dD]([c-fC-F][0-9a-fA-F]{0,2})?)?)?)?$/.test(low)) break
        // Followed by something that is not a low half: drop the orphan.
        i += 6
        continue
      }
      text += String.fromCharCode(code, parseInt(low.slice(2), 16))
      i += 12
      continue
    }
    if (code < 0xdc00 || code > 0xdfff) text += String.fromCharCode(code)
    i += 6
  }
  return { text, end: -1 }
}

function skipSpace(raw: string, i: number): number {
  while (i < raw.length && /\s/.test(raw[i])) i += 1
  return i
}

/** Index just past the value starting at `i`, or -1 when it is unfinished. */
function skipValue(raw: string, i: number): number {
  const ch = raw[i]
  if (ch === undefined) return -1
  if (ch === '"') return scanString(raw, i).end
  if (ch === '{' || ch === '[') {
    let depth = 0
    while (i < raw.length) {
      const c = raw[i]
      if (c === '"') {
        i = scanString(raw, i).end
        if (i < 0) return -1
        continue
      }
      if (c === '{' || c === '[') depth += 1
      else if (c === '}' || c === ']') {
        depth -= 1
        if (depth === 0) return i + 1
      }
      i += 1
    }
    return -1
  }
  // A number or a literal runs until the next separator.
  while (i < raw.length && !/[\s,}\]]/.test(raw[i])) i += 1
  return i < raw.length ? i : -1
}

/**
 * The top-level string field `key` of the partial JSON object `raw`: what has
 * been written of it so far, or null when its value has not started (or is
 * not a string). A key of the same name inside a nested value never matches.
 */
export function partialStringField(raw: string, key: string): PartialString | null {
  let i = skipSpace(raw, 0)
  if (raw[i] !== '{') return null
  i += 1
  while (i < raw.length) {
    i = skipSpace(raw, i)
    if (raw[i] === ',') i = skipSpace(raw, i + 1)
    if (raw[i] !== '"') return null
    const name = scanString(raw, i)
    if (name.end < 0) return null
    i = skipSpace(raw, name.end)
    if (raw[i] !== ':') return null
    i = skipSpace(raw, i + 1)
    if (i >= raw.length) return null
    if (name.text === key) {
      if (raw[i] !== '"') return null
      const value = scanString(raw, i)
      return { text: value.text, closed: value.end >= 0 }
    }
    i = skipValue(raw, i)
    if (i < 0) return null
  }
  return null
}
