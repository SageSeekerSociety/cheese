// Shared living-doc markdown machinery.
//
// ONE extension list powers three things: the DocPanel editor, the lossy-load
// check that guards saves, and the round-trip test corpus. If the editor and
// the check ever used different schemas, the check would be meaningless — so
// they can't: both import from here.
//
// 军规 1 (never silently drop content): the doc is markdown, and the panel
// reads and writes it whole. Any syntax the visual editor can't represent
// would be destroyed by a load→save cycle, so `compareRoundTrip` detects that
// at LOAD time and DocPanel pauses autosave + shows a banner. The escape hatch
// is source mode, which edits the raw markdown and can never be lossy.

import type { AnyExtension } from '@tiptap/core'
import type { ImageOptions } from '@tiptap/extension-image'
import type { Node as PMNode } from '@tiptap/pm/model'
import type { marked, TokenizerExtension } from 'marked'

import { Extension, InputRule, mergeAttributes, Node } from '@tiptap/core'
import CodeBlockLowlight from '@tiptap/extension-code-block-lowlight'
import Image from '@tiptap/extension-image'
import { ListItem, TaskItem, TaskList } from '@tiptap/extension-list'
import { TableKit } from '@tiptap/extension-table'
import { Markdown } from '@tiptap/markdown'
import StarterKit from '@tiptap/starter-kit'
import { common, createLowlight } from 'lowlight'
import { Marked } from 'marked'
import markedCjkFriendly from 'marked-cjk-friendly'

// One lowlight instance (common ≈ 37 languages), shared by every editor.
export const lowlight = createLowlight(common)

// CommonMark's flanking rules make a closing `**` that is preceded by
// punctuation and followed by a letter unable to close — so
// `**执行档案（ExecutionProfile）**解析` is not bold, and neither is
// `**这句。**下一句`. English never hits it because a space always follows the
// delimiter; Chinese has no such space, so it hits constantly. The CJK-friendly
// extension (CommonMark issue #650) counts CJK characters as punctuation for
// flanking, which supplies exactly the missing escape hatch and leaves
// non-CJK text alone.
export const docMarked = new Marked(markedCjkFriendly())
// Tiptap adds editor-only tokenizers to docMarked. Fidelity rendering must use
// an independent standard Markdown parser so unsupported nodes stay visible.
const fidelityMarked = new Marked(markedCjkFriendly())

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

docMarked.use({ extensions: [DocLiteralTag, DocLiteralTagBlock, DocAutolinkCjk] })

// ---- Image: display resolves workspace-relative paths to the raw-file API,
// but the node ATTR keeps the original path — markdown serialization reads the
// attr, so `![](uploads/x.png)` is written back verbatim, never a localhost URL.
export interface DocImageOptions {
  /** Rewrites a markdown src for DISPLAY only (e.g. uploads/x.png → /api/...). */
  resolveSrc: (src: string) => string
}

const DocImage = Image.extend<DocImageOptions & ImageOptions>({
  addOptions() {
    return {
      // parent always exists for an .extend()ed extension; `!` keeps the
      // strict production build honest about the non-optional base options.
      ...this.parent!(),
      resolveSrc: (src: string) => src,
    }
  },
  renderHTML({ HTMLAttributes }) {
    const attrs: Record<string, unknown> = { ...HTMLAttributes }
    if (typeof attrs.src === 'string' && attrs.src) {
      attrs.src = this.options.resolveSrc(attrs.src)
    }
    return ['img', mergeAttributes(this.options.HTMLAttributes, attrs)]
  },
})

// ---- Code block: lowlight highlighting + a data-language attribute on <pre>
// so CSS can show the language corner tag (content: attr(data-language)).
const DocCodeBlock = CodeBlockLowlight.extend({
  addKeyboardShortcuts() {
    return {
      ...this.parent?.(),
      // Tab must INDENT inside a code block, not throw focus out of the
      // editor. Single caret inserts \t; a multi-line selection indents
      // every touched line. Shift-Tab dedents (one \t or up to 2 spaces).
      Tab: () => {
        if (!this.editor.isActive(this.name)) return false
        const { state } = this.editor
        const { from, to } = state.selection
        const multiline = state.doc.textBetween(from, to, '\n').includes('\n')
        if (!multiline) return this.editor.commands.insertContent('\t')
        const text = state.doc.textBetween(from, to, '\n')
        const indented = text
          .split('\n')
          .map((l) => '\t' + l)
          .join('\n')
        return this.editor.commands.insertContentAt({ from, to }, indented)
      },
      'Shift-Tab': () => {
        if (!this.editor.isActive(this.name)) return false
        const { state } = this.editor
        const { from } = state.selection
        const $from = state.doc.resolve(from)
        const lineStart = from - $from.parentOffset
        const blockText = $from.parent.textContent
        // Locate the current line's start inside the block.
        const beforeCaret = blockText.slice(0, $from.parentOffset)
        const relLineStart = beforeCaret.lastIndexOf('\n') + 1
        const absLineStart = lineStart + relLineStart
        const line = blockText.slice(relLineStart)
        const m = line.match(/^(\t| {1,2})/)
        if (!m) return true // nothing to dedent — swallow the key anyway
        return this.editor.commands.deleteRange({
          from: absLineStart,
          to: absLineStart + m[1].length,
        })
      },
    }
  },
  renderHTML({ node, HTMLAttributes }) {
    return [
      'pre',
      mergeAttributes(
        this.options.HTMLAttributes,
        HTMLAttributes,
        node.attrs.language ? { 'data-language': node.attrs.language } : {}
      ),
      [
        'code',
        {
          class: node.attrs.language ? this.options.languageClassPrefix + node.attrs.language : null,
        },
        0,
      ],
    ]
  },
})

// ---- Typing `[text](url)` turns into a real link as you close the paren.
// This parses OUR structured syntax (markdown), not natural language.
const LINK_INPUT_RE = /\[([^\][]+)\]\((\S+?)\)$/
const MarkdownLinkInput = Extension.create({
  name: 'markdownLinkInput',
  addInputRules() {
    return [
      new InputRule({
        find: LINK_INPUT_RE,
        handler: ({ state, range, match }) => {
          const linkMark = state.schema.marks.link
          if (!linkMark) return
          const [, text, href] = match
          state.tr.replaceWith(range.from, range.to, state.schema.text(text, [linkMark.create({ href })]))
          // Don't carry the link mark into whatever is typed next.
          state.tr.removeStoredMark(linkMark)
        },
      }),
    ]
  },
})

export interface DocExtensionsOptions {
  /** Display-time image src resolver (defaults to identity). */
  resolveImageSrc?: (src: string) => string
}

// Keep standalone Markdown comments as invisible document nodes. Dropping them
// would lose source annotations and correctly trip the save-fidelity guard.
const DocComment = Node.create({
  name: 'docComment',
  group: 'block',
  atom: true,
  selectable: false,
  addAttributes() {
    return { source: { default: '', rendered: false } }
  },
  parseHTML() {
    return [
      { tag: 'div[data-doc-comment]', getAttrs: (element) => ({ source: element.getAttribute('data-doc-comment') }) },
    ]
  },
  renderHTML({ node }) {
    return ['div', { 'data-doc-comment': node.attrs.source, hidden: '', 'aria-hidden': 'true' }]
  },
  parseMarkdown: (token, helpers) => helpers.createNode('docComment', { source: token.text }),
  renderMarkdown: (node) => node.attrs?.source ?? '',
  markdownTokenizer: {
    name: 'docComment',
    level: 'block',
    start: (source) => source.search(/^ {0,3}<!--/m),
    tokenize(source) {
      const match = source.match(/^ {0,3}<!--[\s\S]*?-->[ \t]*(?:\n|$)/)
      if (!match) return undefined
      return { type: 'docComment', raw: match[0], text: match[0].trimEnd() }
    },
  },
})

const DocListItem = ListItem.extend({
  renderMarkdown(node, helpers, context) {
    if (context.parentType !== 'orderedList' || context.meta?.parentAttrs?.type) {
      return ListItem.config.renderMarkdown!(node, helpers, context)
    }
    // CommonMark nests beneath the content column: "1. " needs three spaces,
    // "10. " needs four. The upstream helper always uses two and flattens it.
    const start = Number(context.meta?.parentAttrs?.start ?? 1)
    const width = `${start + (context.index ?? 0)}. `.length
    return ListItem.config.renderMarkdown!(node, { ...helpers, indent: (text) => ' '.repeat(width) + text }, context)
  },
})

/** The full extension list for the living-doc editor (and its tests). */
export function docExtensions(opts: DocExtensionsOptions = {}): AnyExtension[] {
  return [
    StarterKit.configure({
      // Replaced by the lowlight-highlighted code block below.
      codeBlock: false,
      listItem: false,
      link: {
        // No click-through plugin: in edit mode a plain click just places the
        // caret (⌘-click opens via DocPanel's delegated handler); in read
        // mode the rendered <a target=_blank> navigates natively.
        openOnClick: false,
        autolink: true,
        linkOnPaste: true,
        HTMLAttributes: { target: '_blank', rel: 'noopener noreferrer' },
      },
    }),
    // The cast: @tiptap/markdown types this option as the marked MODULE, but
    // reads only Lexer/defaults/use/lexer/setOptions off it — all present on an
    // instance, which is what its own README passes. `getDefaults` is the one
    // module-only member, and nothing in the package calls it.
    Markdown.configure({ marked: docMarked as unknown as typeof marked }),
    DocComment,
    TableKit.configure({ table: { resizable: false } }),
    DocListItem,
    TaskList,
    TaskItem.configure({ nested: true }),
    DocImage.configure({
      resolveSrc: opts.resolveImageSrc ?? ((s: string) => s),
    }),
    DocCodeBlock.configure({ lowlight }),
    MarkdownLinkInput,
  ]
}

// ---------------------------------------------------------------------------
// Round-trip comparison (lossy-load detection)
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
//  14. Blank lines between adjacent list-item markers are presentation-only;
//      indentation and paragraph breaks remain strict.
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
//
// Note on our structured tokens: the serializer HTML-escapes `<`/`&` in text,
// which would corrupt `<@handle>` / `<#topicId>` / `<&path>` on save. That's
// why DocPanel must serialize through `serializeDoc` below, which reverses the
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
function mapProse(md: string, fn: (seg: string) => string): string {
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

/** The span `docReplaceRange` says has to be rewritten. */
export interface DocReplaceRange {
  /** First position in the current doc that differs. */
  from: number
  /** End of the differing span, in the CURRENT doc. */
  to: number
  /** End of the differing span, in the INCOMING doc. */
  sliceTo: number
}

/** Where a newly-loaded server version actually differs from what is on screen.
 *
 * Installing a reload by replacing the whole document (`replaceWith(0, size,
 * next)`) maps every position through a step that deleted everything, so a caret
 * parked in a paragraph that did NOT change lands at the end of the document.
 * The living doc is an editor you can click into just to point at a line, and
 * clicking does not make it dirty — so 芝士's next update arrives with someone's
 * caret sitting in it, and yanks it away. Replacing only the span that differs
 * leaves every position before that span alone.
 *
 * NOT a repaint optimisation: prosemirror-view already diffs node by node and
 * reuses the DOM of unchanged children on a full replace. What a full replace
 * costs is positions — selection, and any decoration that has to be mapped.
 *
 * Returns null when the two documents are identical (nothing to do). Otherwise
 * `from`/`to` are positions in `current` and the replacement is
 * `next.slice(from, sliceTo)`.
 */
export function docReplaceRange(current: PMNode, next: PMNode): DocReplaceRange | null {
  const from = current.content.findDiffStart(next.content)
  if (from === null) return null
  const ends = current.content.findDiffEnd(next.content)
  // findDiffStart already said they differ, so findDiffEnd cannot be null; the
  // guard keeps the types honest rather than guarding a reachable case.
  if (!ends) return null
  let { a: to, b: sliceTo } = ends
  // The tail match can run PAST the head match when one side is shorter (delete
  // a paragraph and the surviving text matches from both directions). Push both
  // ends forward by the overlap so the range stays well-formed.
  const overlap = from - Math.min(to, sliceTo)
  if (overlap > 0) {
    to += overlap
    sliceTo += overlap
  }
  return { from, to, sliceTo }
}

/** Serialize the editor to markdown, restoring our structured tokens. */
export function serializeDoc(editor: { getMarkdown: () => string }): string {
  return mapProse(editor.getMarkdown(), (seg) =>
    restoreLiteralTags(
      seg
        .replace(ESCAPED_TOKEN_RE, (_m, inner: string) => `<${inner.replace(/^&amp;/, '&')}>`)
        .replace(AUTOLINK_RT_RE, '$1')
        .replace(EMAIL_AUTOLINK_RT_RE, '$1')
        .replace(INERT_ESCAPE_RE, '$1')
    )
  )
}

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
const BLOCK_START_RE = /^ {0,3}(#{1,6}(\s|$)|<!--|>|([-*+]|\d{1,9}[.)])(\s|$)|(```|~~~)|\||((\*|-|_)\s*){3,}$)/
// A heading is a block all by itself, so whatever follows it starts a new one.
const HEADING_RE = /^ {0,3}#{1,6}(\s|$)/

// The per-line rules that apply to prose wherever it appears. Table cells and
// blockquote bodies are prose too — running only part of this on them is how
// `| login_security.py |` came to report a document as unsafe to edit.
function prose(line: string): string {
  return decodeBasicEntities(unescapeIntrawordUnderscores(line))
}

export function normalizeMarkdown(md: string): string {
  const lines = md.replace(/\r\n?/g, '\n').split('\n')
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
      if (!next?.literal && listItem.test(collapsed.at(-1) ?? '') && listItem.test(next?.text ?? '')) continue
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
