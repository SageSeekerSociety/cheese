// Message rendering, extracted from ChatPanel so it's unit-testable.
//
// References are encoded tokens (not guessed-from-prose): `<@handle>` for a
// teammate, `<#topicId>` for a topic/its doc. Both 芝士 and the composer emit
// them; we render each as a clickable chip showing the name/title. The
// handle→name and id→title maps are provided by the caller (roster / topics).
import type { Block } from '../cx_types'

import DOMPurify from 'dompurify'
import { marked } from 'marked'

export interface RefMaps {
  mentionNames: Record<string, string>
  topicTitles: Record<string, string>
}

export function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

function tokenChip(kind: string, id: string, maps: RefMaps): string {
  if (kind === '@') {
    const name = maps.mentionNames[id] || id
    return `<span class="mention" data-handle="${id}">@${escapeHtml(name)}</span>`
  }
  if (kind === '&') {
    // 文件引用: <&backend/app/main.py> → 一枚文件图标 + main.py 的 chip，点开文件。
    // 图标走 @mdi/font 的字体类（全局 CSS）而不是 emoji：emoji 在各系统上是彩色
    // 位图，字号、基线、颜色都不跟着正文走，混在一行字里很脏。
    const base = id.split('/').pop() || id
    return `<span class="mention file-ref" data-file="${escapeHtml(id)}" title="${escapeHtml(id)}"><i class="mdi mdi-file-document-outline file-ref__icon" aria-hidden="true"></i>${escapeHtml(base)}</span>`
  }
  const title = maps.topicTitles[id] || '话题'
  return `<span class="mention topic-ref" data-topic="${id}">#${escapeHtml(title)}</span>`
}

// Expand reference tokens to chip spans AFTER escaping/markdown: marked (and our
// escape) turn "<@h>" into "&lt;@h&gt;", so we match that escaped form and swap in
// the chip HTML — injecting raw <span> *before* marked would get re-escaped.
const ESCAPED_TOKEN = /&lt;([@#])([\w-]+)&gt;|&lt;(&amp;|&)([\w./\u4e00-\u9fff-]+)&gt;/g

export function highlightTokens(html: string, maps: RefMaps): string {
  return html.replace(ESCAPED_TOKEN, (_m, k, id, _fk, fid) =>
    fid ? tokenChip('&', fid, maps) : tokenChip(k, id, maps)
  )
}

// 芝士's markdown replies → safe HTML (spec §3: AI 必须说人话, 可读).
// breaks:true — this is chat: a single newline the author typed IS a line
// break; strict-markdown paragraph rules would silently swallow it.
export function renderMarkdown(text: string, maps: RefMaps): string {
  return DOMPurify.sanitize(
    highlightTokens(marked.parse(text, { async: false, gfm: true, breaks: true }) as string, maps)
  )
}

// Plain (non-markdown) human text → escape, expand reference tokens, keep
// newlines. The newlines survive here as literal \n; the host element must
// render with `white-space: pre-wrap` or the browser collapses them.
export function renderPlain(text: string, maps: RefMaps): string {
  return DOMPurify.sanitize(highlightTokens(escapeHtml(text), maps))
}

interface FenceState {
  marker: '`' | '~'
  length: number
}

/**
 * Advance CommonMark fenced-code state across one stored message fragment.
 *
 * Historical turns (before the backend coalesced SDK partial messages) can have
 * the opening fence, code body, and closing fence in separate Block rows. Each
 * row is valid data, but parsing each row independently renders the two fences
 * as empty grey boxes and the body as headings/prose. Tracking only line-level
 * fences is deliberately narrow: ordinary consecutive chat messages remain
 * independent.
 */
function scanFenceState(text: string, initial: FenceState | null): FenceState | null {
  let state = initial
  for (const line of text.split(/\r?\n/)) {
    const match = line.match(/^ {0,3}(`{3,}|~{3,})(.*)$/)
    if (!match) continue
    const fence = match[1]
    const marker = fence[0] as '`' | '~'
    const suffix = match[2]
    if (state === null) {
      // CommonMark forbids a backtick in a backtick fence's info string.
      if (marker === '`' && suffix.includes('`')) continue
      state = { marker, length: fence.length }
    } else if (marker === state.marker && fence.length >= state.length && suffix.trim() === '') {
      state = null
    }
  }
  return state
}

function sameLegacyMessageRun(a: Block, b: Block): boolean {
  if (
    a.kind !== 'message' ||
    b.kind !== 'message' ||
    a.author_type !== 'ai' ||
    b.author_type !== 'ai' ||
    a.author !== b.author
  ) {
    return false
  }
  // A real turn id is a hard boundary. Null IDs occur on older affected rows;
  // those are safe to join only while they remain physically consecutive.
  return a.turn_id || b.turn_id ? a.turn_id === b.turn_id : true
}

/**
 * Compatibility repair for historical split fenced-code messages.
 *
 * Only a run containing an unmatched opening fence is coalesced, and only up
 * to its matching close. IDs/reactions stay anchored on the first fragment so
 * existing links and reaction actions remain stable.
 */
export function coalesceSplitFencedCodeBlocks(blocks: Block[]): Block[] {
  const out: Block[] = []
  for (let i = 0; i < blocks.length; i += 1) {
    const first = blocks[i]
    let state = scanFenceState(first.content, null)
    if (state === null || first.kind !== 'message' || first.author_type !== 'ai') {
      out.push(first)
      continue
    }

    let content = first.content
    let end = i
    while (state !== null && end + 1 < blocks.length) {
      const next = blocks[end + 1]
      if (!sameLegacyMessageRun(first, next)) break
      content += next.content
      state = scanFenceState(next.content, state)
      end += 1
    }

    // No matching close before a hard boundary: leave the source rows untouched.
    // Guessing across an incomplete fence would hide otherwise independent
    // messages and make the compatibility layer more destructive than the bug.
    if (end === i || state !== null) {
      out.push(first)
      continue
    }
    out.push({ ...first, content })
    i = end
  }
  return out
}
