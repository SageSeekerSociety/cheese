import type { FileSource } from '../cx_types'

/** 读者指出的那一处东西本身，连同它所依据的那一版文件。
 *
 *  两种形状：整页（或页里选中的一段文字）和页面上的一点。「指哪儿」这件事在两种
 *  查看器里本来就不一样，所以各带各的字段；共同的部分是文件身份和版本。 */
export type QuotedContext = SlidePageQuote | PagePinQuote

/** Original source data; never expanded or passed through mention rendering. */
export type SlidePageQuote = Readonly<{
  kind: 'slide-page'
  path: string
  source: FileSource
  version: string
  task_id: string | null
  page: number
  /**
   * 读者指的是整页还是这一页里的一段。
   *
   * 有了它，`text` 才有了确定的说法：`page` 时是整页文字，`selection` 时是选中的
   * 那一段。少了它两件事从消息上分不开 —— 受话人会拿选中的一行当整页看。
   *
   * 可选是为了读得懂已经在库里的消息：那些消息写在那之前，一律当整页。
   * 写出去的时候一定带上（`PanelPreviewView.vue` 的 `sendLocator`）。
   */
  scope?: 'page' | 'selection'
  text: string
}>

/** 一页上的一点：`x`/`y` 是这一页宽高的比例（0..1），不是像素。
 *
 *  用比例是因为这一页会按面板宽度重画、读者也会缩放：像素坐标只对当时那一版成立，
 *  比例对哪一版都指得回同一处。 */
export type PagePinQuote = Readonly<{
  kind: 'page-pin'
  path: string
  source: FileSource
  version: string
  task_id: string | null
  page: number
  x: number
  y: number
}>

export function frozenQuote(quote: QuotedContext): QuotedContext {
  return Object.freeze({ ...quote })
}

/** 两种引文共有的那一半：文件身份 + 版本 + 页码。
 *
 *  尺子照着后端 `PagePinQuoteIn`/`SlidePageQuoteIn` 来：`path`/`version` 后端收
 *  `min_length=1`，空串发过去是 422，这里先拦下。`task_id` 的 UUID 形式不做检查
 *  ——它只由后端给出（或为 null），前端从不自己造，多一条正则只是重复后端的规则。 */
function hasIdentity(q: Record<string, unknown>): boolean {
  return (
    typeof q.path === 'string' &&
    q.path.length > 0 &&
    (q.source === 'live' || q.source === 'committed') &&
    typeof q.version === 'string' &&
    q.version.length > 0 &&
    (q.task_id === null || typeof q.task_id === 'string') &&
    typeof q.page === 'number' &&
    Number.isInteger(q.page) &&
    q.page > 0
  )
}

/** 比例是闭区间里的数：0 和 1 都合法（贴左边、贴顶边的那一点）。 */
function isRatio(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1
}

export function isQuotedContext(value: unknown): value is QuotedContext {
  if (!value || typeof value !== 'object') return false
  const q = value as Record<string, unknown>
  if (!hasIdentity(q)) return false
  if (q.kind === 'slide-page') {
    return (q.scope === undefined || q.scope === 'page' || q.scope === 'selection') && typeof q.text === 'string'
  }
  if (q.kind === 'page-pin') return isRatio(q.x) && isRatio(q.y)
  return false
}
