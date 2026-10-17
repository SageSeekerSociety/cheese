// The `:::name` container syntax: a name, at least three colons, the rest of
// the opening line as its info, and a closer with the same colon count. Nested
// containers close with their own count, so `::::columns` can hold `:::column`.
//
// Shared by every container block (./timeline.ts, ./stats.ts, ./columns.ts,
// ./chart.ts) and by the two containers that have a module of their own
// (./canvas.ts, ./app.ts).

const OPENER = /^(:{3,})([a-z]+)[ \t]*(.*)$/
const CLOSER = /^(:{3,})[ \t]*$/

/** The container starting at the head of `src`: its colon count, name, the rest
 *  of the opening line, its body, and the raw text it spans. A nested container
 *  closes with its own colon count, so `::::columns` can hold `:::column`. */
export function readContainer(src: string): { name: string; info: string; body: string; raw: string } | null {
  const lines = src.split('\n')
  const head = OPENER.exec(lines[0])
  if (!head) return null
  const open = head[1].length
  const stack: number[] = []
  for (let i = 1; i < lines.length; i++) {
    const nested = OPENER.exec(lines[i])
    if (nested) {
      stack.push(nested[1].length)
      continue
    }
    const close = CLOSER.exec(lines[i])
    if (!close) continue
    const n = close[1].length
    if (stack.length && stack[stack.length - 1] === n) {
      stack.pop()
      continue
    }
    if (!stack.length && n === open) {
      const raw = lines.slice(0, i + 1).join('\n') + (i + 1 < lines.length ? '\n' : '')
      return { name: head[2], info: head[3].trim(), body: lines.slice(1, i).join('\n'), raw }
    }
  }
  return null
}

/** Where a `:::name` container could start, for marked's tokenizer `start`. */
export function containerStart(name: string) {
  const re = new RegExp(`^:{3,}${name}\\b`, 'm')
  return (src: string) => src.search(re)
}

/** `- a | b | c` → ['a', 'b', 'c'] (the dash optional). Splits on the first
 *  separators only, so the last field keeps any `|` it has. */
export function fieldsOf(line: string, count: number): string[] {
  const text = line.replace(/^\s*[-*+][ \t]+/, '')
  const parts = text.split(/[ \t]*\|[ \t]*/)
  const head = parts.slice(0, count - 1)
  const tail = parts.slice(count - 1).join(' | ')
  return [...head, tail].map((part) => part.trim())
}

/** The start of a line that carries one item of a container's body. */
export const ITEM_LINE = /^[-*+][ \t]+/
