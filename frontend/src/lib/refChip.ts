// How a reference written into a message or a document looks: `<@handle>` a
// person, `<#id>` a channel, a task or a 支线, `<&path>` a file (with an optional line range,
// `<&src/a.ts:12-30>`). The raw token stays in the text; what a reader sees is
// a chip with the name on it. Chat, the document editor and the reader all
// draw it from here, so a reference reads the same everywhere.
//
// Clicks are not wired here: the page around the chip delegates them by the
// data attribute (data-handle / data-topic / data-file). A chip outside an editor
// is a button to the keyboard too: it takes Tab focus, and Enter or Space on it is
// a click (`installRefChipKeys`), so the same delegated handlers open it. The
// focusability is written as attributes because chat serialises chips to HTML.
import { t } from '../i18n'
import { agentTone } from '../utils/avatar'

import { isAgentHandle } from './authorship'
import { lookupRef } from './refDirectory'

export interface RefNames {
  /** handle → display name. */
  mentionNames: Record<string, string>
  /** handle → hover tooltip when display name is a personal remark (original name + @handle). */
  mentionTitles?: Record<string, string>
  /** Titles the page already holds (its channels and tasks), by id. Any other
   *  `<#id>` is looked up (lib/refDirectory). */
  topicTitles: Record<string, string>
  /**
   * Which `<@handle>` here notified its person. Only those are drawn as the
   * solid @chip; every other one is a name reference — the person's name with
   * no "@" and no fill — because "@" and the solid chip say "this told them",
   * and a notice, a report or an agent's words in a document told nobody.
   * `true`: every one did (a message that went through the mention path).
   * Absent: none did.
   */
  notified?: boolean | ((handle: string) => boolean)
  /**
   * In a members-only channel, the people a message's @ can notify there (its
   * members); anyone else named in it was not told. Absent: anyone can be.
   */
  reachable?: ReadonlySet<string>
  /**
   * Draw an @ that names an AI teammate as the teammate itself — its small face and
   * name on an amber wash — rather than the solid @chip. The composer does: there that
   * chip says "this goes to the teammate", which the button beside Send mirrors.
   */
  agentFaces?: boolean
  /**
   * What a question to 芝士 cites, by the id in its `<#id>`: its kind and name. A
   * library file (`file-<id>`) or a 题目 (`problem-<id>`) is not a conversation or a
   * document, so nothing else could name it; the panel knows each from when it was
   * picked, or from what the question kept.
   */
  cited?: Record<string, { kind: 'channel' | 'task' | 'doc' | 'file' | 'problem'; title: string }>
}

const CITED_ICON = { doc: 'mdi-file-document-outline', file: 'mdi-paperclip', problem: 'mdi-trophy-outline' }

/** What a message's `<@handle>` told, with `names` as the room hands them over:
 *  each one notified its person, except someone a members-only channel cannot
 *  reach. `<@all>` / `<@here>` are the room itself. */
export function toldByMessage(names: RefNames): RefNames {
  const reachable = names.reachable
  return {
    ...names,
    notified: reachable ? (handle) => handle === 'all' || handle === 'here' || reachable.has(handle) : true,
  }
}

/** Whether the `<@handle>` in text drawn with `names` notified its person. */
function didNotify(handle: string, names: RefNames): boolean {
  const n = names.notified
  return typeof n === 'function' ? n(handle) : n === true
}

type TopicRefShown = { text: string; missing: boolean; document?: boolean; unreadable?: boolean }

/** What a `<#id>` reads as. One that names nothing says so: it never borrows a
 *  name, and a reader is not sent into a channel that is not there. */
function topicRef(id: string, names: RefNames): TopicRefShown {
  const title = names.topicTitles[id]
  if (title) return { text: `#${title}`, missing: false }
  const found = lookupRef(id)
  if (found === 'missing') return { text: `#${t('work.room.chat.refMissing')}`, missing: true }
  if (found === undefined) return { text: '#…', missing: false }
  // 无权查看的东西不露标题：只说它在，你看不了。
  if (found.kind === 'unreadable') return { text: t('work.room.chat.refUnreadable'), missing: true, unreadable: true }
  if (found.kind === 'document')
    return { text: found.title || t('work.room.doc.untitled'), missing: false, document: true }
  if (found.kind === 'thread')
    return { text: `#${t('work.room.chat.refThread', { title: found.title })}`, missing: false }
  return { text: `#${found.title}`, missing: false }
}

// 资料库保留上传原名，重名时追加 (n)：括号也是文件路径的一部分。
export const REF_TOKEN = /<([@#])([\w-]+)>|<&([\w./一-鿿()-]+(?::\d+(?:-\d+)?)?)>/g

/**
 * `focusable: false` for a chip inside an editable document: there the caret moves
 * through the text with the arrow keys, and a Tab stop per chip would pull focus
 * out of the writing.
 */
export function refChip(
  kind: '@' | '#' | '&',
  id: string,
  names: RefNames,
  opts: { focusable?: boolean } = {}
): HTMLElement {
  const el = document.createElement('span')
  el.className = 'mention'
  const cited = kind === '#' ? names.cited?.[id] : undefined
  if (cited) {
    if (cited.kind === 'channel' || cited.kind === 'task') {
      el.classList.add('topic-ref')
      el.dataset.topic = id
      el.textContent = `#${cited.title}`
    } else {
      el.classList.add('file-ref')
      if (cited.kind === 'doc') {
        el.classList.add('doc-ref')
        el.dataset.topic = id
      }
      const icon = document.createElement('i')
      icon.className = `mdi ${CITED_ICON[cited.kind]} file-ref__icon`
      icon.setAttribute('aria-hidden', 'true')
      el.append(icon, document.createTextNode(cited.title))
    }
  } else if (kind === '#') {
    const shown = topicRef(id, names)
    el.classList.add('topic-ref')
    el.textContent = shown.text
    if (shown.missing) {
      // 没有去处：不是按钮，不进 Tab 顺序，点了也没有父级会接（没有 data-topic）。
      el.classList.add('ref-missing')
      el.title = t(shown.unreadable ? 'work.room.chat.refUnreadableHint' : 'work.room.chat.refMissingHint')
      return el
    }
    el.dataset.topic = id
    if (shown.document) {
      // 一份文档：和文件一样是图标 + 标题，不带 #。点了照样走 data-topic，去处由 refRoute 认。
      el.classList.remove('topic-ref')
      el.classList.add('file-ref', 'doc-ref')
      const icon = document.createElement('i')
      icon.className = 'mdi mdi-file-document-outline file-ref__icon'
      icon.setAttribute('aria-hidden', 'true')
      el.replaceChildren(icon, document.createTextNode(shown.text))
    }
  } else if (kind === '@') {
    el.dataset.handle = id
    const hoverTitle = names.mentionTitles?.[id]
    if (hoverTitle) el.title = hoverTitle
    if (didNotify(id, names) && names.agentFaces && isAgentHandle(id)) {
      el.classList.add('agent-ref')
      el.append(agentFace(id), document.createTextNode(names.mentionNames[id] || id))
    } else if (didNotify(id, names)) el.textContent = `@${names.mentionNames[id] || id}`
    else {
      el.className = 'name-ref'
      el.textContent = names.mentionNames[id] || id
    }
  } else {
    // 一枚文件图标 + 文件名，点开文件。图标走 @mdi/font 的字体类而不是 emoji：
    // emoji 在各系统上是彩色位图，字号、基线、颜色都不跟着正文走。
    el.classList.add('file-ref')
    el.dataset.file = id
    el.title = id
    const icon = document.createElement('i')
    icon.className = 'mdi mdi-file-document-outline file-ref__icon'
    icon.setAttribute('aria-hidden', 'true')
    el.append(icon, document.createTextNode(id.split('/').pop() || id))
  }
  if (opts.focusable !== false) {
    el.setAttribute('role', 'button')
    el.setAttribute('tabindex', '0')
  }
  return el
}

const SVG = 'http://www.w3.org/2000/svg'

/** The teammate's face as the chip's leading mark: the same tile and eyes as CheeseAvatar, in its tone. */
function agentFace(handle: string): SVGElement {
  const svg = document.createElementNS(SVG, 'svg')
  svg.setAttribute('class', 'agent-ref__face')
  svg.setAttribute('viewBox', '0 0 100 100')
  svg.setAttribute('aria-hidden', 'true')
  svg.dataset.tone = String(agentTone(handle))
  const tile = document.createElementNS(SVG, 'path')
  tile.setAttribute('d', 'M50 0C88 0 100 12 100 50S88 100 50 100S0 88 0 50S12 0 50 0Z')
  svg.append(tile)
  for (const [x, y, w, h, r] of [
    [42.5, 44.5, 13, 21, 6.5],
    [66, 46, 10, 16, 5],
  ]) {
    const eye = document.createElementNS(SVG, 'rect')
    eye.setAttribute('x', String(x))
    eye.setAttribute('y', String(y))
    eye.setAttribute('width', String(w))
    eye.setAttribute('height', String(h))
    eye.setAttribute('rx', String(r))
    svg.append(eye)
  }
  return svg
}

/** Each reference token in `text`, with where it sits. */
export function refTokens(text: string): { index: number; length: number; kind: '@' | '#' | '&'; id: string }[] {
  const out: { index: number; length: number; kind: '@' | '#' | '&'; id: string }[] = []
  for (const m of text.matchAll(REF_TOKEN)) {
    out.push({
      index: m.index,
      length: m[0].length,
      kind: m[3] ? '&' : (m[1] as '@' | '#'),
      id: (m[2] ?? m[3]) as string,
    })
  }
  return out
}

/** The same references read as words in a line of plain text: @名字, #频道名, 文件名. */
export function plainRefs(text: string, names: RefNames): string {
  return text.replace(REF_TOKEN, (_m, k: string, id: string, fid: string) => {
    if (fid) return fid.split('/').pop() || fid
    if (k === '@') return didNotify(id, names) ? `@${names.mentionNames[id] || id}` : names.mentionNames[id] || id
    const cited = names.cited?.[id]
    if (cited) return cited.kind === 'channel' || cited.kind === 'task' ? `#${cited.title}` : cited.title
    return topicRef(id, names).text
  })
}

/**
 * Enter or Space on a focused chip clicks it, once for the whole page: chips in
 * chat are written as HTML, so they cannot carry a listener of their own.
 */
export function installRefChipKeys(root: Document = document): void {
  root.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ') return
    const chip = e.target instanceof HTMLElement ? e.target : null
    if (!chip?.matches('.mention[role="button"], .name-ref[role="button"]') || chip.isContentEditable) return
    e.preventDefault()
    chip.click()
  })
}
