/**
 * 把一条评论记下的那一处，在**今天**渲染出来的正文里找回来。
 *
 * 评论存的是「哪一版 + 那段原文 + 标题路径 + 前后各 32 字」（`markdownQuote` 取的
 * 形状），不存任何 DOM 位置：DOM 每次重渲染都换一批节点，位置存下来立刻过期。所以
 * 反过来——拿那段原文回正文里找，找不到就不画。
 *
 * 两道判据，和当初取的时候一一对应：
 *
 * - 原文本身。空白先归一（渲染出来的换行和缩进跟原文对不上），再在归一后的全文里找。
 * - 哪里出现的那一次。一段话在正文里往往不止一处，靠标题路径和前后文分辨；两个候选
 *   一样像的时候宁可不画，也别把标记钉到另一句话上。
 */

import { headingPath, normaliseText } from './markdownQuote'

export type AnchorQuote = Readonly<{
  /** 选中的原文（取的时候已经归过空白）。 */
  text: string
  /** 那一节，形如 `一级 › 二级`；文件开头是空串。 */
  heading: string | null
  prefix: string
  suffix: string
}>

type Slot = Readonly<{ node: Text; offset: number }>

/** 这些地方的字不是读者看到的正文，找位置时跳过：脚本、样式、我们自己画的东西。 */
const SKIP = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT'])

function collectText(root: HTMLElement): Text[] {
  const out: Text[] = []
  // 自己走 childNodes，不用 TreeWalker：测试环境（happy-dom）对它的数字 filter 不认，
  // 传 SHOW_TEXT 会一个节点都不给——那样在浏览器里对、在测试里永远找不到位置。
  const visit = (node: Node): void => {
    if (node.nodeType === Node.TEXT_NODE) {
      const text = node as Text
      if (!text.data) return
      const parent = text.parentElement
      if (parent && (SKIP.has(parent.tagName) || parent.closest('[data-anchor-ignore]'))) return
      out.push(text)
      return
    }
    for (const child of Array.from(node.childNodes)) visit(child)
  }
  visit(root)
  return out
}

/** 全文按归一后的样子拼一遍，并且记住每个字来自哪个节点、第几个字。
 *
 *  归一指的是把每一段连续的空白压成一个空格、两端去掉——和 `normaliseText` 同一把
 *  尺子（`/\s/`，**不是**只认空格和换行：中文文档里的全角空格 U+3000、不换行空格
 *  U+00A0 都算空白），取的时候是这么量的，找的时候就得这么量。只认 ASCII 空白的话，
 *  原文里一个全角空格，存下的那句话就和这里的正文永远对不上，那一处再也不会画出来。 */
function flatten(root: HTMLElement): { text: string; slots: Slot[] } {
  const slots: Slot[] = []
  let text = ''
  // 那一段空白里最后一个字的出处：压缩成的那一个空格要指向它，而不是「当前字的前一格」——
  // 空白可能整段都在上一个文字节点里（`甲 ` + `乙`），那时 `i - 1` 在这个节点上是 -1。
  let pendingSpace: Slot | null = null
  for (const node of collectText(root)) {
    const data = node.data
    for (let i = 0; i < data.length; i += 1) {
      const ch = data[i]
      if (/\s/.test(ch)) {
        // 一段空白只留一个空格，而且要等下一个非空白字到了才留：末尾的空白等于没有。
        if (text) pendingSpace = { node, offset: i }
        continue
      }
      if (pendingSpace) {
        text += ' '
        slots.push(pendingSpace)
        pendingSpace = null
      }
      text += ch
      slots.push({ node, offset: i })
    }
  }
  return { text, slots }
}

/** 归一后的 `text` 里，`needle` 每一次出现在哪（返回值是开始下标）。 */
function occurrences(text: string, needle: string): number[] {
  const found: number[] = []
  if (!needle) return found
  for (let at = text.indexOf(needle); at !== -1; at = text.indexOf(needle, at + 1)) found.push(at)
  return found
}

function rangeOf(slots: Slot[], from: number, to: number): Range {
  const start = slots[from]
  const end = slots[to - 1]
  const range = document.createRange()
  range.setStart(start.node, start.offset)
  range.setEnd(end.node, end.offset + 1)
  return range
}

/** 这一段像不像评论说的那一次：标题对上算两分，前后文各算一分。
 *
 *  `text` 是归一后的全文，由调用方传进来——每个候选都重新拼一遍全文的话，一份长
 *  文档里有几处候选就要走几遍 DOM。 */
function likeness(
  root: HTMLElement,
  slots: Slot[],
  text: string,
  at: number,
  quote: AnchorQuote,
  length: number
): number {
  let score = 0
  const heading = quote.heading?.trim()
  if (heading) {
    // 标题取的是「到这个节点为止」的那条路径，所以拿候选的起点去问同一句话。
    if (headingPath(root, slots[at].node) === heading) score += 2
  }
  const before = normaliseText(quote.prefix ?? '')
  const after = normaliseText(quote.suffix ?? '')
  if (before && text.slice(Math.max(0, at - before.length), at).endsWith(before)) score += 1
  if (after && text.slice(at + length, at + length + after.length).startsWith(after)) score += 1
  return score
}

/** 那一处在今天的正文里的位置，找不到就是 null。
 *
 *  宁可不画也不乱画：两个候选一样像的时候返回 null——把标记钉到另一句话上，比没有
 *  标记更坏。 */
export function locateQuote(root: HTMLElement | null, quote: AnchorQuote): Range | null {
  if (!root) return null
  const wanted = normaliseText(quote.text ?? '')
  if (!wanted) return null
  const { text, slots } = flatten(root)
  const hits = occurrences(text, wanted)
  if (!hits.length) return null
  if (hits.length === 1) return rangeOf(slots, hits[0], hits[0] + wanted.length)

  const scored = hits.map((at) => ({ at, score: likeness(root, slots, text, at, quote, wanted.length) }))
  const best = Math.max(...scored.map((entry) => entry.score))
  // 一道判据管到底：一处都没得分时，所有候选并列最高分，于是也走下面那一句返回 null。
  // 曾经在它前面另写一句「最高分是 0 就别画」，那一句永远轮不到自己生效（每处的得分只
  // 加不减，0 分必然是全体并列），却让「一处都没得分」那条用例拦在它这里、拦不到真正
  // 做判断的那一句。
  const top = scored.filter((entry) => entry.score === best)
  if (top.length !== 1) return null
  return rangeOf(slots, top[0].at, top[0].at + wanted.length)
}

/** 标记外套在正文上的那个属性：它同时是「这里有一条评论」和「这条评论是哪一条」。 */
export const MARK_ATTR = 'data-file-comment'

/** 把已经画上去的标记拆掉，正文恢复原样。
 *
 *  画之前必须先拆：正文是 `MarkdownView` 自己渲染的 DOM，标记只能从外面包一层，而
 *  上一轮包出来的 `mark` 里的字仍然会被当成正文——不拆就画，一段话会被越包越深，按
 *  文字找位置时也会把 `mark` 的边界算进去。拆完把相邻文字节点合回去（`normalize`），
 *  否则正文被切成好几段，下一次的 Range 跨不过它们。 */
export function clearMarks(root: HTMLElement | null): void {
  if (!root) return
  for (const mark of Array.from(root.querySelectorAll<HTMLElement>(`[${MARK_ATTR}]`))) {
    const parent = mark.parentNode
    if (!parent) continue
    while (mark.firstChild) parent.insertBefore(mark.firstChild, mark)
    parent.removeChild(mark)
  }
  root.normalize()
}

/** 攒着的那几处挂在正文上的序号（原型里的 ②③）：它是**另一种**元素，不是评论的
 *  `mark`——点它不开评论栏，按字找位置时也不该把它当正文。 */
export const PIN_ATTR = 'data-pending-pin'

/** 正文里不参与「按字找位置」和「取原文/前后文」的东西挂这个属性。
 *
 *  正文是 `MarkdownView` 渲染出来的，而标记和序号是我们从外面插进去的：`collectText`
 *  跳过挂了这个属性的元素，`quoteFromSelection`/`contextAround` 取出的原文也不含它们。
 *  没有它的话，读者选一句正好跨过一颗序号时，交给芝士的原文里会多一个数字。 */
export const ANCHOR_IGNORE_ATTR = 'data-anchor-ignore'

/** 把上一轮挂上去的序号拆掉。和 `clearMarks` 分开：两种标记谁先画谁后画都要拆干净，
 *  而 `clearMarks` 只认 `MARK_ATTR`，挂了序号不拆就会越积越多。 */
export function clearPins(root: HTMLElement | null): void {
  if (!root) return
  for (const pin of Array.from(root.querySelectorAll<HTMLElement>(`[${PIN_ATTR}]`))) pin.remove()
  root.normalize()
}

/** 把一个 Range 包进一颗 `mark`，返回包出来的每一颗（按文档顺序）；包不成返回 null。
 *
 *  返回的是**全部**，不是第一颗：一句话里夹着加粗或链接时，这一处被切成好几个文字节点，
 *  每一段各包一颗 `mark`。只交回一颗的话，外面给它接的点击只有那一段点得动——读者点在
 *  别的几段上，一颗看得见、点不开的标记。
 *
 *  从最里往外包：一包完，外面那一层的偏移就不作数了，所以先包最后一个文字节点、再往
 *  前。跨节点的选区逐个文字节点按 Range 的偏移切。 */
export function wrapRange(range: Range, id: string, active: boolean): HTMLElement[] | null {
  const parts: { node: Text; from: number; to: number }[] = []
  const visit = (node: Node): void => {
    if (node.nodeType === Node.TEXT_NODE) {
      const text = node as Text
      if (!range.intersectsNode(text)) return
      const from = text === range.startContainer ? range.startOffset : 0
      const to = text === range.endContainer ? range.endOffset : text.data.length
      if (to > from) parts.push({ node: text, from, to })
      return
    }
    for (const child of Array.from(node.childNodes)) visit(child)
  }
  visit(range.commonAncestorContainer)
  if (!parts.length) return null

  const marks: HTMLElement[] = []
  for (const part of parts.reverse()) {
    // 只核偏移还在不在这一段里：正文没连在文档上也照样能包（测试里就是那样），
    // 真正会出事的是上一次 splitText 之后偏移过期了。
    if (part.to > part.node.data.length || part.from >= part.node.data.length) return null
    const piece = part.node.splitText(part.from)
    piece.splitText(part.to - part.from)
    const mark = document.createElement('mark')
    mark.setAttribute(MARK_ATTR, id)
    mark.className = active ? 'file-comment-mark is-active' : 'file-comment-mark'
    piece.parentNode?.replaceChild(mark, piece)
    mark.appendChild(piece)
    marks.push(mark)
  }
  // 按文档顺序交回：外面拿它当「这一段被切成哪几颗」来用，顺序和读的顺序对不上会让人
  // 以为第一颗就是最左边那颗。
  return marks.length ? marks.reverse() : null
}
