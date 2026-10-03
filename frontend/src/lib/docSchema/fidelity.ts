// Whether a Markdown document survives a trip through the editor: what the
// editor would write back, compared with what was read.
//
// Part of the document schema module (see ./index.ts): nothing here imports
// the app.

import { Marked } from 'marked'
import markedCjkFriendly from 'marked-cjk-friendly'

import { mapProse } from './markdown'

// Tiptap adds editor-only tokenizers to docMarked. Fidelity rendering must use
// an independent standard Markdown parser so unsupported nodes stay visible.
const fidelityMarked = new Marked(markedCjkFriendly())

// ---------------------------------------------------------------------------
// Round-trip comparison: what converting a Markdown document would change
// ---------------------------------------------------------------------------
//
// `normalizeMarkdown` defines the TOLERATED differences between the markdown
// on disk and what the editor would write back. Anything beyond these rules
// counts as lossy. The rules, explicitly:
//
//   1. Line endings: CRLF/CR → LF.
//   2. Trailing whitespace on each line is ignored.
//   3. Runs of blank lines collapse to one — the editor models a blank line
//      as block separation, "how many" is not representable.
//   4. Leading/trailing blank lines of the document are ignored.
//   5. Table rows: cell padding spaces collapse (| a  | → | a |) and
//      separator dashes collapse (----- → ---) keeping `:` alignment
//      markers — GFM ignores both, so does every renderer.
//   6. Bullet markers `*` / `+` normalize to `-` (same list, different pen).
//   7. Blockquote markers normalize to `> ` per nesting level.
//   8. "Empty" ATX heading trailing #s are NOT normalized (rare, keep strict).
//  11. Intraword `\_` unescapes to `_` outside inline code (CommonMark:
//      intraword underscores never toggle emphasis — escape is pure noise).
//  12. Block boundaries always carry a blank line. The serializer writes one
//      between every pair of blocks; a document written without it (`正文\n##
//      小节`) parses identically. Both sides get the blank INSERTED, never
//      removed, so a serializer that merged two blocks into one still fails —
//      merging changes the text of the lines, not just the space between them.
//  13. A paragraph's continuation lines carry no indentation of their own.
//      `1. 第一条` + a 4-space continuation is the same list item as the same
//      text continued at 2 spaces; only the item markers state the nesting, and
//      those are left strict. A line that follows a BLANK line keeps its indent,
//      which is what leaves 4-space indented code blocks strict.
//  14. Blank lines before a list-item marker, after another item or after an
//      item's indented content, are presentation-only; indentation and
//      paragraph breaks remain strict.
//  15. A fold's `<summary>` may sit on the line after `<details>`, and
//      `<details open>` folds the same text: both are the one-line head the
//      serializer writes. A footnote definition, a fold's head and its
//      `</details>` are blocks of their own, so rule 12 spaces them too.
//
// Everything else — dropped constructs, reordered content, lost alignment,
// lost language tags, escaped-away tokens — fails the comparison.

//   9. Lines consisting solely of blockquote markers (`>`) are dropped — the
//      serializer emits them as block separators inside a quote.
//  10. Basic HTML entities (&lt; &gt; &quot; &#39; &amp;) decode to their
//      characters outside code fences — the serializer entity-encodes bare
//      `<` / `&` in prose; same characters, different encoding. (Accepted
//      blind spot: entities inside INLINE code spans also get decoded for
//      comparison; a construct that rare fails safe elsewhere.)

const TABLE_ROW_RE = /^\s*\|.*\|\s*$/
const TABLE_SEP_CELL_RE = /^\s*(:?)-+(:?)\s*$/

// Rule 10: decode the five basic entities. `&lt;` first, `&amp;` LAST so a
// literal `&amp;lt;` decodes to `&lt;` (one level), never all the way to `<`.
function decodeBasicEntities(s: string): string {
  return s
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&amp;/g, '&')
}

// Rule 11: `proj\_id` ≡ `proj_id` between word characters — CommonMark says
// intraword underscores never open/close emphasis, so the serializer's escape
// there is pure noise (bare identifiers in prose are everywhere in our docs).
// Inline code spans are left untouched (split on backtick segments).
function unescapeIntrawordUnderscores(line: string): string {
  return line
    .split('`')
    .map((seg, i) => (i % 2 === 0 ? seg.replace(/(?<=[\w\u4e00-\u9fff])\\_(?=[\w\u4e00-\u9fff])/g, '_') : seg))
    .join('`')
}

function normalizeTableRow(line: string): string {
  // Split naïvely on '|': escaped pipes are rare and both sides of the
  // comparison get the same treatment, so equality is still meaningful.
  const inner = line.trim().slice(1, -1)
  const cells = inner.split('|').map((c) => {
    const sep = c.match(TABLE_SEP_CELL_RE)
    if (sep) return `${sep[1]}---${sep[2]}`
    return c.trim().replace(/\s+/g, ' ')
  })
  return `| ${cells.join(' | ')} |`
}

// A line that opens a block of its own: heading, quote, list item, fence,
// table row, thematic break. Everything else continues the block above it.
const BLOCK_START_RE =
  /^ {0,3}(#{1,6}(\s|$)|<!--|>|([-*+]|\d{1,9}[.)])(\s|$)|(```|~~~)|\||((\*|-|_)\s*){3,}$|:{3,}|\$\$|\[\^[^\]\s]+\]:|<\/?details)/
// A heading is a block all by itself, so whatever follows it starts a new one.
// So is a footnote definition, and the head of a fold.
const HEADING_RE = /^ {0,3}(#{1,6}(\s|$)|\[\^[^\]\s]+\]:|<details><summary>)/
const DETAILS_HEAD_RE = /^( {0,3})<details(?:\s+open)?>[ \t]*\n?[ \t]*<summary>/gim

// The per-line rules that apply to prose wherever it appears. Table cells and
// blockquote bodies are prose too — running only part of this on them is how
// `| login_security.py |` came to report a document as unsafe to edit.
function prose(line: string): string {
  return decodeBasicEntities(unescapeIntrawordUnderscores(line))
}

export function normalizeMarkdown(md: string): string {
  const lines = md.replace(/\r\n?/g, '\n').replace(DETAILS_HEAD_RE, '$1<details><summary>').split('\n')
  // Each entry keeps whether the line is fence content, so the blank-line
  // collapse below never touches the inside of a code block.
  const out: { text: string; literal: boolean }[] = []
  let inFence = false
  for (let raw of lines) {
    const fence = raw.match(/^(\s*)(```|~~~)/)
    if (fence) inFence = !inFence
    if (inFence || fence) {
      // Inside code fences everything is literal — only strip trailing WS on
      // the fence lines themselves, keep code lines byte-exact.
      out.push({ text: fence ? raw.trimEnd() : raw, literal: !fence })
      continue
    }
    raw = raw.replace(/\s+$/, '')
    if (TABLE_ROW_RE.test(raw)) {
      out.push({ text: prose(normalizeTableRow(raw)), literal: false })
      continue
    }
    // A line of only quote markers is the serializer's block separator inside
    // a quote — structural noise for comparison purposes: drop it.
    if (/^(\s{0,3}>\s*)+$/.test(raw)) {
      continue
    }
    // Blockquote markers: ">text", "> text", ">  > text" → "> " per level.
    const bq = raw.match(/^((?:\s{0,3}>\s?)+)(.*)$/)
    if (bq) {
      const depth = (bq[1].match(/>/g) ?? []).length
      raw = '> '.repeat(depth) + bq[2].trim()
      out.push({ text: prose(raw.trimEnd()), literal: false })
      continue
    }
    // Bullet markers * / + → - (preserve indentation).
    raw = raw.replace(/^(\s*)[*+](\s)/, '$1-$2')
    out.push({ text: prose(raw), literal: false })
  }
  // Rules 12 & 13: put a blank line on every block boundary, and drop the
  // indent a paragraph's continuation lines were wrapped at. Both read the
  // fence flag, so code keeps its own spacing byte-exact.
  const spaced: { text: string; literal: boolean }[] = []
  for (const l of out) {
    const prev = spaced[spaced.length - 1]
    if (!l.literal && prev && !prev.literal && prev.text !== '') {
      const boundary =
        HEADING_RE.test(prev.text) ||
        /-->$/.test(prev.text) ||
        /^ {0,3}\[\^[^\]\s]+\]:/.test(l.text) ||
        (!BLOCK_START_RE.test(prev.text) && BLOCK_START_RE.test(l.text))
      if (boundary && l.text !== '') spaced.push({ text: '', literal: false })
    }
    const continuation = !l.literal && prev && !prev.literal && prev.text !== '' && !BLOCK_START_RE.test(l.text)
    spaced.push(continuation ? { text: l.text.replace(/^ +(?=\S)/, ''), literal: false } : l)
  }

  // Collapse blank-line runs (never inside fences); trim document edges.
  const collapsed: string[] = []
  let prevBlank = false
  for (const [index, l] of spaced.entries()) {
    if (!l.literal && l.text === '') {
      // List-item spacing changes tight/loose presentation, not item content.
      // Keep paragraph breaks and list indentation strict.
      const next = spaced.slice(index + 1).find((line) => line.text !== '')
      const listItem = /^ {0,3}(?:[-*+]|\d+[.)])\s/
      // So is a blank line before the next item after an item's indented
      // content (a closing fence, a second paragraph).
      const prev = collapsed.at(-1) ?? ''
      if (!next?.literal && (listItem.test(prev) || /^ {2,}\S/.test(prev)) && listItem.test(next?.text ?? '')) continue
      if (prevBlank) continue
      prevBlank = true
    } else {
      prevBlank = false
    }
    collapsed.push(l.text)
  }
  while (collapsed[0] === '') collapsed.shift()
  while (collapsed[collapsed.length - 1] === '') collapsed.pop()
  return collapsed.join('\n')
}

export interface RoundTripReport {
  clean: boolean
  /** First few differing normalized lines, for console.debug forensics. */
  diff: string
}

/** Compare original markdown vs its parse→serialize round trip. */
export function compareRoundTrip(original: string, roundTripped: string): RoundTripReport {
  const a = normalizeMarkdown(original)
  const b = normalizeMarkdown(roundTripped)
  if (a === b) return { clean: true, diff: '' }
  // Escaped literal punctuation and equivalent emphasis delimiters can differ
  // byte-for-byte while preserving the document. Compare the independent
  // inline Markdown renderer, not the editor's parsed tree (which could already
  // have dropped unsupported HTML). Block syntax and code stay strict.
  const rendered = (md: string) =>
    mapProse(md, (seg) =>
      fidelityMarked
        .parseInline(seg, { async: false })
        .replace(/<(strong|em)>(<a\b[^>]*>)([\s\S]*?)<\/a><\/\1>/g, '$2<$1>$3</$1></a>')
    )
  if (rendered(a) === rendered(b)) return { clean: true, diff: '' }
  const al = a.split('\n')
  const bl = b.split('\n')
  const diffs: string[] = []
  const n = Math.max(al.length, bl.length)
  for (let i = 0; i < n && diffs.length < 12; i++) {
    if (al[i] !== bl[i]) {
      diffs.push(`  L${i + 1}`)
      diffs.push(`  - ${al[i] ?? '∅'}`)
      diffs.push(`  + ${bl[i] ?? '∅'}`)
    }
  }
  return { clean: false, diff: diffs.join('\n') }
}
