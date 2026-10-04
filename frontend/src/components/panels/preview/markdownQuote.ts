/**
 * 读者在渲染好的 markdown 里选中的一段话，以及它在全文里的位置。
 *
 * 交给芝士的是一个普通句子，不是能长期保留的锚点：读者要改的那句话，正是下一轮要
 * 改掉的那句话，锚点必然失效（这条道理见 `PanelPreviewView` 里「指出位置」那一节）。
 * 所以这里只取三样——原文、最近的标题路径、前后各 32 字。后两样是给受话人找位置的：
 * 一句 `重试 3 次` 在一份文档里往往不止一处，标题和上下文才是能分辨说的是哪一句的
 * 东西。形状照着 Claude 桌面版那边的 text-quote 取（exact / prefix / suffix，两侧
 * 各 32 字符也是它用的宽度），只是不存下来。
 */

/** 选区两侧各取多少个字符。 */
export const CONTEXT_CHARS = 32
/** 标题路径太长时从前面砍掉：深的那几节才是读者说「这里」的地方。 */
export const MAX_HEADING_PATH = 80

export type MarkdownQuote = Readonly<{
  /** 选中的原文，空白已经归一到单个空格。 */
  text: string
  /** 这段文字两侧的那 32 个字。 */
  prefix: string
  suffix: string
  /** 这一段属于哪一节，形如 `一级 › 二级`。它之前没有标题时是空串。 */
  heading: string
}>

/** 选中那段文字的两侧，交给受话人分辨同一句话的几处出现（就是上面那两段的形状）。 */
export type QuoteContext = Readonly<Pick<MarkdownQuote, 'prefix' | 'suffix'>>

export function normaliseText(raw: string): string {
  return raw.replace(/\s+/g, ' ').trim()
}

/** 从 `node` 往前看，最近的标题路径。 */
export function headingPath(root: HTMLElement, node: Node): string {
  const stack: { level: number; text: string }[] = []
  // 这个 tsconfig 下 NodeList 不能直接 for...of，先摊成数组。
  for (const heading of Array.from(root.querySelectorAll<HTMLElement>('h1, h2, h3, h4, h5, h6'))) {
    // 文档顺序里第一个不在选区之前的标题之后，就没有在它前面的标题了。
    if (!comesBefore(heading, node)) break
    const level = Number(heading.tagName.slice(1))
    // 同级或更浅的标题关掉比它深的那几节：这是标题层级自己的规矩。
    while (stack.length && stack[stack.length - 1].level >= level) stack.pop()
    const text = normaliseText(heading.textContent ?? '')
    if (text) stack.push({ level, text })
  }
  return clipStart(stack.map((entry) => entry.text).join(' › '), MAX_HEADING_PATH)
}

function comesBefore(earlier: Node, later: Node): boolean {
  const relation = earlier.compareDocumentPosition(later)
  return Boolean(relation & (Node.DOCUMENT_POSITION_FOLLOWING | Node.DOCUMENT_POSITION_CONTAINED_BY))
}

function clipStart(text: string, max: number): string {
  const points = codePoints(text)
  return points.length > max ? `…${points.slice(1 - max).join('')}` : text
}

/** 按码点切开，别把 emoji 这类代理对劈成半个，屏幕上会渲染成一个问号。 */
function codePoints(text: string): string[] {
  return Array.from(text)
}

/** 选区两侧的文字：`prefix` 取选区之前那一段的末尾，`suffix` 取之后的头一段。
 *
 *  正文和幻灯片共用这一条：两边都是「一块放得下选区、也放得下它前后文的元素」，
 *  分辨同一句话的哪一处出现，靠的是同一套归一化和同一道 32 字的上限。 */
export function contextAround(root: HTMLElement, range: Range): { prefix: string; suffix: string } {
  const before = document.createRange()
  before.selectNodeContents(root)
  before.setEnd(range.startContainer, range.startOffset)
  const after = document.createRange()
  after.selectNodeContents(root)
  after.setStart(range.endContainer, range.endOffset)
  return {
    prefix: codePoints(normaliseText(before.toString())).slice(-CONTEXT_CHARS).join(''),
    suffix: codePoints(normaliseText(after.toString())).slice(0, CONTEXT_CHARS).join(''),
  }
}

/** 这次选中能不能交给芝士。指不成的都返回 null：没选、太短、或者跨出了这块正文。 */
export function quoteFromSelection(root: HTMLElement | null, selection: Selection | null): MarkdownQuote | null {
  if (!root || !selection || selection.rangeCount === 0 || selection.isCollapsed) return null
  const range = selection.getRangeAt(0)
  if (!root.contains(range.startContainer) || !root.contains(range.endContainer)) return null
  const text = normaliseText(selection.toString())
  // 一两个字的选中多半是双击落的词或误碰，指不了任何地方——和幻灯片那边同一道坎。
  if (text.length < 2) return null
  return { text, ...contextAround(root, range), heading: headingPath(root, range.startContainer) }
}
