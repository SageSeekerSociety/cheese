import type { FileSource } from '../cx_types'

/** 读者指出的那一处东西本身，连同它所依据的那一版文件。
 *
 *  幻灯片整页（或页里选中的一段文字）、页面上的一点、表格里的一格、渲染出来的正文里
 *  选中的一段，再加上网页预览里的三种：点一个元素、选一段文字、在没有注入桥的应用上
 *  圈一块区域。「指哪儿」这件事在每种查看器里本来就不一样，所以各带各的字段；共同的
 *  部分是文件身份和版本（`FileIdentity`）——只有 `web-region` 例外：一个应用没有版本
 *  可言，它凭页面地址定位（见 `WebRegionQuote`）。 */
export type QuotedContext =
  | SlidePageQuote
  | PagePinQuote
  | SheetCellQuote
  | TextRangeQuote
  | WebElementQuote
  | WebTextQuote
  | WebRegionQuote

/** 带文件身份的那些引文共有的那一半：文件身份 + 版本。少了哪一样都指不回同一份东西。
 *
 *  尺子照着后端的输入模型来：`path`/`version` 后端收 `min_length=1`，空串发过去是
 *  422，这里先拦下。`task_id` 的 UUID 形式不做检查 ——它只由后端给出（或为 null），
 *  前端从不自己造，多一条正则只是重复后端的规则。 */
type FileIdentity = Readonly<{
  path: string
  source: FileSource
  version: string
  task_id: string | null
}>

/** Original source data; never expanded or passed through mention rendering. */
export type SlidePageQuote = Readonly<
  FileIdentity & {
    kind: 'slide-page'
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
    /**
     * 选中那一段两侧的文字，帮受话人分辨同一句话的哪一处出现：一句 `重试 3 次`
     * 在一页上往往不止一处，前后文才是能分辨说的是哪一句的东西（形状见
     * `markdownQuote.ts`，和 `text-range` 用的是同一条）。
     *
     * 可选是为了读得懂已经在库里的消息 —— 那些消息写在那之前，一律当整页。整页的
     * 引用不带这两样（它本来就是整页）。写出去的时候选中一段一定带上
     * （`usePreviewQuote.ts` 的 `send`）。
     */
    prefix?: string
    suffix?: string
  }
>

/** 一页上的一点：`x`/`y` 是这一页宽高的比例（0..1），不是像素。
 *
 *  用比例是因为这一页会按面板宽度重画、读者也会缩放：像素坐标只对当时那一版成立，
 *  比例对哪一版都指得回同一处。 */
export type PagePinQuote = Readonly<
  FileIdentity & {
    kind: 'page-pin'
    page: number
    x: number
    y: number
  }
>

/** 表格里的一格：`address` 是 A1 那个写法，`value` 是这一格当时的内容。
 *
 *  和工作表一样，`address` 是文件本身给得出来的地址，受话人拿它直接就能回到那一格，
 *  不像页码要经过一次转换。CSV 没有工作表名，`sheet` 就是空串。 */
export type SheetCellQuote = Readonly<
  FileIdentity & {
    kind: 'sheet-cell'
    sheet: string
    address: string
    value: string
  }
>

/** 渲染出来的正文里选中的一段：`text` 是原文，`heading`/`prefix`/`suffix` 帮受话人分辨
 *  同一句话的哪一处出现（形状见 `markdownQuote.ts`）。
 *
 *  正文没有页码，位置说的是「在哪一节之下」：改一句话要重新渲染，行号下一版就不成立，
 *  标题和前后文才是能带回原文里找的东西。没有标题时 `heading` 是 null（文件开头）。 */
export type TextRangeQuote = Readonly<
  FileIdentity & {
    kind: 'text-range'
    text: string
    heading: string | null
    prefix: string
    suffix: string
  }
>

/** 网页里量出来的一块地方：`x`/`y` 是这一块左上角在页面视口里的像素，`w`/`h` 是它的
 *  宽高，都按当时的视口量。像素而不是比例，是因为一个网页就是按这个视口画的，「当时
 *  那个大小」本身就是它的一部分。 */
export type WebRect = Readonly<{ x: number; y: number; w: number; h: number }>

/** 量这一块时视口有多大——页面按视口重排，少了它 `rect` 说的是哪一版就说不准了。 */
export type WebViewport = Readonly<{ w: number; h: number }>

/** 网页预览里点中的一个元素。
 *
 *  `selector` 是一段短的、稳定的 CSS 路径，受话人拿它就能回到页面上那一处；`text` 是
 *  它当时看得见的文字（截到 500 字）。两者合起来才是「指的是哪个元素」：光有选择器
 *  认不出内容换没换，光有文字在页面上又不止一处。 */
export type WebElementQuote = Readonly<
  FileIdentity & {
    kind: 'web-element'
    selector: string
    tag: string
    text: string
    rect: WebRect
    viewport: WebViewport
  }
>

/** 网页预览里选中的一段文字。`prefix`/`suffix` 是选中那一段两侧各 32 个字符，帮受话人
 *  分辨同一句话在页面上的哪一处出现（形状和 `text-range` 用的是同一条）。 */
export type WebTextQuote = Readonly<
  FileIdentity & {
    kind: 'web-text'
    selector: string
    tag: string
    text: string
    prefix: string
    suffix: string
    rect: WebRect
    viewport: WebViewport
  }
>

/** 一个没有注入桥的应用上圈出的一块区域：只有页面地址、这块地方和当时的视口，没有页面
 *  内容 —— 那个页面在别的源上，宿主也读不到它的 DOM。没有文件身份：应用没有「那一版
 *  文件」可指，地址就是它唯一的身份。 */
export type WebRegionQuote = Readonly<{
  kind: 'web-region'
  url: string
  rect: WebRect
  viewport: WebViewport
}>

export function frozenQuote(quote: QuotedContext): QuotedContext {
  return Object.freeze({ ...quote })
}

/** 四种引文共有的那一半：文件身份 + 版本。 */
function hasFileIdentity(q: Record<string, unknown>): boolean {
  return (
    typeof q.path === 'string' &&
    q.path.length > 0 &&
    (q.source === 'live' || q.source === 'committed') &&
    typeof q.version === 'string' &&
    q.version.length > 0 &&
    (q.task_id === null || typeof q.task_id === 'string')
  )
}

/** 页码是正整数。 */
function isPage(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value > 0
}

/** 比例是闭区间里的数：0 和 1 都合法（贴左边、贴顶边的那一点）。 */
function isRatio(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1
}

/** 一段限长的字符串。上限照着后端的字段来：选择器和地址一路进提示词，不设限就是给它
 *  开了个后门。空串算不算由调用方说（`min`）。 */
function isText(value: unknown, min: number, max: number): value is string {
  return typeof value === 'string' && value.length >= min && value.length <= max
}

/** 一个数。像素坐标可以是负的（元素被滚到了视口左边、上边），只要是有限数。 */
function isCoordinate(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

/** 一个尺寸：非负的有限数。矩形宽高可以是 0（空元素），但不该是负的。 */
function isLength(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
}

/** 一个正的尺寸：视口大小用。窗口量出来总是大于 0，0 说明这一处没法还原成「哪一版的
 *  版面」——后端也不收（`gt=0`），这里先拦下，好让它落回普通那句话，而不是发出去吃
 *  一个 422。 */
function isPositiveLength(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value > 0
}

/** 量出来的一块地方。 */
function isWebRect(value: unknown): boolean {
  if (!value || typeof value !== 'object') return false
  const r = value as Record<string, unknown>
  return isCoordinate(r.x) && isCoordinate(r.y) && isLength(r.w) && isLength(r.h)
}

/** 视口大小：两个都必须是正的（后端 `gt=0`）。 */
function isWebViewport(value: unknown): boolean {
  if (!value || typeof value !== 'object') return false
  const v = value as Record<string, unknown>
  return isPositiveLength(v.w) && isPositiveLength(v.h)
}

export function isQuotedContext(value: unknown): value is QuotedContext {
  if (!value || typeof value !== 'object') return false
  const q = value as Record<string, unknown>
  // 应用上圈的一块区域是唯一不带文件身份的：它凭地址定位，先单独认下来。
  if (q.kind === 'web-region') {
    return isText(q.url, 1, 2048) && isWebRect(q.rect) && isWebViewport(q.viewport)
  }
  if (!hasFileIdentity(q)) return false
  // 网页里点中的元素 / 选中的一段文字：选择器和标签是回到页面那一处的凭据。
  if (q.kind === 'web-element' || q.kind === 'web-text') {
    if (!isText(q.selector, 1, 256) || !isText(q.tag, 0, 32)) return false
    if (!isWebRect(q.rect) || !isWebViewport(q.viewport)) return false
    if (q.kind === 'web-element') return isText(q.text, 0, 500)
    return isText(q.text, 1, 500) && isText(q.prefix, 0, 64) && isText(q.suffix, 0, 64)
  }
  if (q.kind === 'slide-page') {
    return (
      isPage(q.page) &&
      (q.scope === undefined || q.scope === 'page' || q.scope === 'selection') &&
      typeof q.text === 'string' &&
      // 前后文可选（库里的老消息没有），但带上了就得是字符串而不是别的东西。
      (q.prefix === undefined || typeof q.prefix === 'string') &&
      (q.suffix === undefined || typeof q.suffix === 'string')
    )
  }
  if (q.kind === 'page-pin') return isPage(q.page) && isRatio(q.x) && isRatio(q.y)
  if (q.kind === 'sheet-cell') {
    // 地址后端收 `min_length=1`；`sheet` 空串是 CSV 的正常形状；`value` 可以是空。
    return (
      typeof q.sheet === 'string' &&
      typeof q.address === 'string' &&
      q.address.length > 0 &&
      typeof q.value === 'string'
    )
  }
  if (q.kind === 'text-range') {
    return (
      typeof q.text === 'string' &&
      (q.heading === null || typeof q.heading === 'string') &&
      typeof q.prefix === 'string' &&
      typeof q.suffix === 'string'
    )
  }
  return false
}
