// How a living document reads and writes Markdown: the parser the editor's
// Markdown extension runs on, and the clean-up its serializer output needs
// before it is the document's text.
//
// Part of the document schema module (see ./index.ts): nothing here imports
// the app.

import type { TokenizerExtension } from 'marked'

import { Marked } from 'marked'
import markedCjkFriendly from 'marked-cjk-friendly'

// CommonMark's flanking rules make a closing `**` that is preceded by
// punctuation and followed by a letter unable to close — so
// `**执行档案（ExecutionProfile）**解析` is not bold, and neither is
// `**这句。**下一句`. English never hits it because a space always follows the
// delimiter; Chinese has no such space, so it hits constantly. The CJK-friendly
// extension (CommonMark issue #650) counts CJK characters as punctuation for
// flanking, which supplies exactly the missing escape hatch and leaves
// non-CJK text alone.

// ---- Two things the markdown parser gets wrong for this editor --------------
//
// 1. A BARE TAG. `<img>`, `<div>`, `</p>` are valid HTML to any markdown
//    parser, so they are tokenized as HTML and handed to the schema — which has
//    no node for a lone `img`/`div`/`p`, so the token is dropped and the tag
//    goes with it. Measured on the project overview: `优先渲染 <img>、@error`
//    came back as `优先渲染 、@error`, and a line that was just `<img>` came
//    back empty. The FILE is the source of truth, so a tag the editor cannot
//    represent has to survive as LITERAL TEXT (军规 1). The claim is narrow on
//    purpose — no attributes, no partner tag in sight — so `<a href="…">见</a>`
//    and `<img src="…">` stay real elements for the schema, exactly as before.
//    `restoreLiteralTags` writes the angle brackets back on save.
//
// 2. AN AUTOLINK NEXT TO CHINESE. GFM runs a bare URL to the next whitespace,
//    and Chinese has none: `见 http://host/status，通了(200，22ms)就说明后端健康`
//    turned the rest of the sentence into the link — href and all. Cut the URL
//    where a reader ends it: the first CJK character or fullwidth punctuation.
//    Without CJK in reach the token is left to marked's own rules.

// A tag with no attributes — `<img>`, `</p>`, `<br/>`. Anything with a space
// inside the angle brackets is a real element and is not claimed.
const BARE_TAG_RE = /^<\/?([a-zA-Z][a-zA-Z0-9-]*)\s*\/?>/

// Tags the editor represents as a NODE on their own, so a lone one is not lost
// and must keep rendering as what it is: `<br>` is a hard break, `<hr>` a
// thematic break. (`<img>` is only a node WITH a src, i.e. with attributes, so
// it never reaches here.)
const NODE_TAGS = new Set(['br', 'hr'])

/** Whether a tag of this name is already open in the tokens of THIS inline run. */
function openedInRun(tokens: { type?: string; raw?: unknown }[], name: string): boolean {
  const open = new RegExp(`<${name}(?=[\\s/>])`, 'i')
  const close = new RegExp(`</${name}\\s*>`, 'i')
  for (let i = tokens.length - 1; i >= 0; i--) {
    if (tokens[i].type !== 'html') continue
    const raw = String(tokens[i].raw ?? '')
    if (close.test(raw)) return false // that element is already closed
    if (open.test(raw)) return true
  }
  return false
}

const DocLiteralTag: TokenizerExtension = {
  name: 'docLiteralTag',
  level: 'inline',
  start: (src) => src.indexOf('<'),
  tokenizer(src, tokens) {
    const match = BARE_TAG_RE.exec(src)
    if (!match) return undefined
    const raw = match[0]
    const name = match[1]
    if (NODE_TAGS.has(name.toLowerCase())) return undefined
    const partner = new RegExp(`</${name}\\s*>`, 'i')
    if (raw.startsWith('</') ? openedInRun(tokens, name) : partner.test(src.slice(raw.length))) {
      return undefined // a real element — leave it to the HTML parser
    }
    return { type: 'text', raw, text: raw }
  },
}

// The same tag alone on a line is a BLOCK-level HTML token, which takes the
// block path in the parser and is dropped just the same. No `start`: this runs
// at every block position anyway, and a `start` makes marked clip and re-merge
// the paragraph above it (measured: the paragraph's inline tokens came back
// holding the wrong text).
const BARE_TAG_LINE_RE = /^ {0,3}<(\/?)([a-zA-Z][a-zA-Z0-9-]*)\s*\/?>[ \t]*(?:\n|$)/

const DocLiteralTagBlock: TokenizerExtension = {
  name: 'docLiteralTagBlock',
  level: 'block',
  tokenizer(src) {
    const match = BARE_TAG_LINE_RE.exec(src)
    if (!match) return undefined
    const [raw, closing, name] = match
    if (NODE_TAGS.has(name.toLowerCase())) return undefined
    if (!closing && new RegExp(`</${name}\\s*>`, 'i').test(src.slice(raw.length))) {
      return undefined // a multi-line element — the HTML block rule owns it
    }
    const tag = raw.trim()
    return { type: 'paragraph', raw, tokens: [{ type: 'text', raw: tag, text: tag }] }
  },
}

// CJK punctuation and symbols, kana, CJK ideographs, and fullwidth forms —
// the characters a Chinese sentence continues with after a bare URL.
const CJK_RE = /[\u2e80-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]/
const AUTOLINK_HEAD_RE = /^(?:https?:\/\/|www\.)[^\s<]+/i

// Marked backpedals trailing punctuation off its own autolinks; after cutting at
// the CJK boundary the same has to happen, or `见 https://x/a，` leaves the
// comma inside the href. An unmatched trailing `)` is not part of the URL.
function trimAutolink(url: string): string {
  let trimmed = url
  for (let prev = ''; prev !== trimmed; ) {
    prev = trimmed
    trimmed = trimmed.replace(/[?!.,:;*_'"~]+$/, '')
    if (trimmed.endsWith(')')) {
      const open = (trimmed.match(/\(/g) ?? []).length
      const close = (trimmed.match(/\)/g) ?? []).length
      if (close > open) trimmed = trimmed.slice(0, -1)
    }
  }
  return trimmed
}

const DocAutolinkCjk: TokenizerExtension = {
  name: 'docAutolinkCjk',
  level: 'inline',
  start: (src) => src.search(/https?:\/\/|www\./i),
  tokenizer(src) {
    if (this.lexer.state.inLink) return undefined
    const match = AUTOLINK_HEAD_RE.exec(src)
    if (!match) return undefined
    const cut = match[0].search(CJK_RE)
    if (cut === -1) return undefined // no CJK in reach: marked's own rules own it
    const raw = trimAutolink(match[0].slice(0, cut))
    if (!raw) return undefined
    const href = /^www\./i.test(raw) ? `http://${raw}` : raw
    return { type: 'link', raw, href, text: raw, tokens: [{ type: 'text', raw, text: raw }] }
  },
}

/** A parser with the document's rules. The chat reads with its own (a single
 *  newline breaks the line there); building it here keeps the rules one list. */
export function buildDocMarked(): Marked {
  return new Marked(markedCjkFriendly()).use({ extensions: [DocLiteralTag, DocLiteralTagBlock, DocAutolinkCjk] })
}

export const docMarked = buildDocMarked()

// Note on our structured tokens: the serializer HTML-escapes `<`/`&` in text,
// which would corrupt `<@handle>` / `<#topicId>` / `<&path>` on save. That's
// why an editor must serialize through `serializeDoc` below, which reverses the
// escaping for EXACTLY those three token shapes (deterministic token syntax —
// never general unescaping, which could turn user-typed literal HTML live).

const ESCAPED_TOKEN_RE = /&lt;(@[\w-]+|#[0-9a-fA-F-]{8,}|&amp;[\w./一-鿿-]+(?::\d+(?:-\d+)?)?)&gt;/g

// A pure autolink serializes as `[url](url)`; write the bare URL back so the
// file stays byte-stable (GFM re-autolinks it on the next parse). The mark's
// renderMarkdown can't do this — the manager splits marks into open/close
// around a placeholder, so text===href is only visible after serialization.
const AUTOLINK_RT_RE = /\[((?:https?:\/\/|mailto:)[^\s\]]+|www\.[^\s\]]+)\]\((?:https?:\/\/)?\1\)/g

// An email autolinks as `[addr@host](mailto:addr@host)`: the visible text has no
// `mailto:` prefix, so AUTOLINK_RT_RE does not recognize it and a save wrote the
// whole link syntax into the file — `a@b.com` came back `[a@b.com](mailto:a@b.com)`
// in a document nobody had touched. Same rule, same reason as the line above.
const EMAIL_AUTOLINK_RT_RE = /\[([^\s\][()]+@[^\s\][()]+)\]\(mailto:\1\)/g

// A bare tag survives parsing as LITERAL text (see DocLiteralTag) and the
// serializer escapes its `<` on the way out, so without this every save rewrites
// `<img>` into `&lt;img&gt;`. Deliberately narrow: no whitespace inside (nothing
// carrying attributes), nothing URL-shaped (`<https://x>` is an autolink, not a
// tag), and never one of the structured tokens restored just above.
const LITERAL_TAG_RT_RE = /&lt;(\/?[^\s<>&]+)&gt;/g

function restoreLiteralTags(segment: string): string {
  return segment.replace(LITERAL_TAG_RT_RE, (match, inner: string) => {
    if (/^[@#&]/.test(inner) || inner.includes('://')) return match
    return `<${inner}>`
  })
}

// The serializer escapes every character that could start a construct, whether
// or not one is possible here — so `P1~P4` comes back `P1\~P4` and `app[bot]`
// comes back `app\[bot\]`, and a save writes that backslash into the file. Drop
// the two that never carry meaning on their own: a lone `~` needs a partner to
// open strikethrough, and a `[` needs a `](` or `][` to open a link. Underscore
// is deliberately NOT here — it pairs across a line often enough that
// unescaping it changes how real documents parse.
const INERT_ESCAPE_RE = /\\([~[\]])/g

// Apply `fn` to prose only: skip fenced code lines entirely and inline code
// spans within a line, so literal `[x](x)` / `&lt;` inside code is never touched.
export function mapProse(md: string, fn: (seg: string) => string): string {
  let inFence = false
  return md
    .split('\n')
    .map((line) => {
      if (/^\s*(```|~~~)/.test(line)) {
        inFence = !inFence
        return line
      }
      if (inFence) return line
      // Chunk the line into code spans (`…`) and prose; transform prose only.
      return line
        .split(/(`+[^`]*`+)/)
        .map((chunk, i) => (i % 2 === 1 ? chunk : fn(chunk)))
        .join('')
    })
    .join('\n')
}

/** Serialize the editor to markdown, restoring our structured tokens. */
export function serializeDoc(editor: { getMarkdown: () => string }): string {
  return finishMarkdown(editor.getMarkdown())
}

/** The serializer's raw output, made into the document's text: our structured
 *  tokens restored and the serializer's noise taken back out. */
export function finishMarkdown(raw: string): string {
  return mapProse(raw, (seg) =>
    restoreLiteralTags(
      seg
        .replace(ESCAPED_TOKEN_RE, (_m, inner: string) => `<${inner.replace(/^&amp;/, '&')}>`)
        .replace(AUTOLINK_RT_RE, '$1')
        .replace(EMAIL_AUTOLINK_RT_RE, '$1')
        .replace(INERT_ESCAPE_RE, '$1')
    )
  )
}
