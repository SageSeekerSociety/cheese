// How a reference written into a message or a document looks: `<@handle>` a
// person, `<#topicId>` a topic, `<&path>` a file (with an optional line range,
// `<&src/a.ts:12-30>`). The raw token stays in the text; what a reader sees is
// a chip with the name on it. Chat, the document editor and the reader all
// draw it from here, so a reference reads the same everywhere.
//
// Clicks are not wired here: the page around the chip delegates them by the
// data attribute (data-handle / data-topic / data-file).
import { t } from '../i18n'

export interface RefNames {
  /** handle → display name. */
  mentionNames: Record<string, string>
  /** topic id → title. */
  topicTitles: Record<string, string>
}

// 资料库保留上传原名，重名时追加 (n)：括号也是文件路径的一部分。
export const REF_TOKEN = /<([@#])([\w-]+)>|<&([\w./一-鿿()-]+(?::\d+(?:-\d+)?)?)>/g

export function refChip(kind: '@' | '#' | '&', id: string, names: RefNames): HTMLElement {
  const el = document.createElement('span')
  el.className = 'mention'
  if (kind === '@') {
    el.dataset.handle = id
    el.textContent = `@${names.mentionNames[id] || id}`
  } else if (kind === '#') {
    el.classList.add('topic-ref')
    el.dataset.topic = id
    el.textContent = `#${names.topicTitles[id] || t('work.room.chat.topicFallback')}`
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
  return el
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

/** The same references read as words in a line of plain text: @名字, #话题名, 文件名. */
export function plainRefs(text: string, names: RefNames): string {
  return text.replace(REF_TOKEN, (_m, k: string, id: string, fid: string) => {
    if (fid) return fid.split('/').pop() || fid
    if (k === '@') return `@${names.mentionNames[id] || id}`
    return `#${names.topicTitles[id] || t('work.room.chat.topicFallback')}`
  })
}
