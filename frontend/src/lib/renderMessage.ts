// Message rendering, extracted from ChatPanel so it's unit-testable.
//
// References are encoded tokens (not guessed-from-prose): `<@handle>` for a
// teammate, `<#topicId>` for a topic/its doc, `<&path>` for a file. Both 芝士
// and the composer emit them; each renders as a clickable chip (lib/refChip.ts).
// 芝士's Markdown is drawn by the reader (components/common/MarkdownView.vue);
// what is here is the rest: a person's own words, and the text put back into
// the box when they edit a message.
import type { Block } from '../cx_types'
import type { RefNames } from './refChip'

import { isAgentBlock } from './authorship'
import { refChip, refTokens } from './refChip'

// 改一条自己发过的消息时，输入框里放的字：点过名的人写回「@名字」，和当初在输入框
// 里打的一样，保存时后端再把名字认回 token。话题和文件引用原样留着 —— 写成标题或
// 文件名的话，保存时就认不回原来那一个了。
const MENTION_TOKEN = /<@([\w-]+)>/g

export function editableText(text: string, maps: RefNames): string {
  return text.replace(MENTION_TOKEN, (_m, handle) => `@${maps.mentionNames[handle] || handle}`)
}

// A person's words: as typed, with each reference token drawn as its chip.
// The newlines survive as literal \n; the host element must render with
// `white-space: pre-wrap` or the browser collapses them.
export function renderPlain(text: string, maps: RefNames): string {
  const box = document.createElement('span')
  let at = 0
  for (const ref of refTokens(text)) {
    box.append(text.slice(at, ref.index), refChip(ref.kind, ref.id, maps))
    at = ref.index + ref.length
  }
  box.append(text.slice(at))
  return box.innerHTML
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
  if (a.kind !== 'message' || b.kind !== 'message' || !isAgentBlock(a) || !isAgentBlock(b) || a.author !== b.author) {
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
    if (state === null || first.kind !== 'message' || !isAgentBlock(first)) {
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
