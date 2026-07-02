// Message rendering, extracted from ChatPanel so it's unit-testable.
//
// References are encoded tokens (not guessed-from-prose): `<@handle>` for a
// teammate, `<#topicId>` for a topic/its doc. Both 芝士 and the composer emit
// them; we render each as a clickable chip showing the name/title. The
// handle→name and id→title maps are provided by the caller (roster / topics).
import { marked } from 'marked'
import DOMPurify from 'dompurify'

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
  const title = maps.topicTitles[id] || '话题'
  return `<span class="mention topic-ref" data-topic="${id}">#${escapeHtml(title)}</span>`
}

// Expand reference tokens to chip spans AFTER escaping/markdown: marked (and our
// escape) turn "<@h>" into "&lt;@h&gt;", so we match that escaped form and swap in
// the chip HTML — injecting raw <span> *before* marked would get re-escaped.
const ESCAPED_TOKEN = /&lt;([@#])([\w-]+)&gt;/g

export function highlightTokens(html: string, maps: RefMaps): string {
  return html.replace(ESCAPED_TOKEN, (_m, k, id) => tokenChip(k, id, maps))
}

// 芝士's markdown replies → safe HTML (spec §3: AI 必须说人话, 可读).
// breaks:true — this is chat: a single newline the author typed IS a line
// break; strict-markdown paragraph rules would silently swallow it.
export function renderMarkdown(text: string, maps: RefMaps): string {
  return DOMPurify.sanitize(
    highlightTokens(
      marked.parse(text, { async: false, gfm: true, breaks: true }) as string,
      maps,
    ),
  )
}

// Plain (non-markdown) human text → escape, expand reference tokens, keep
// newlines. The newlines survive here as literal \n; the host element must
// render with `white-space: pre-wrap` or the browser collapses them.
export function renderPlain(text: string, maps: RefMaps): string {
  return DOMPurify.sanitize(highlightTokens(escapeHtml(text), maps))
}
