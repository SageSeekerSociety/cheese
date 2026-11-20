import type { FileSource } from '@/cx_types'

/** File identity supplied by the owning authorized byte reader, not inferred from pixels. */
export type SlideSource = {
  topicId: string
  path: string
  source: FileSource
  taskId?: string | null
  version: string
}
/** 读者在幻灯片上指的是哪儿：整页，还是这一页里的一段。
 *
 * `text` 跟着 `scope` 变：`page` 时是整页文字，`selection` 时是选中的那一段。
 * 两条都从同一个冻结引用出去 —— 选中一句不比指整页少带什么身份；在加这一条之前
 * 它只是一句拼好的中文，连版本都没有。 */
export type SlidePageContext = {
  text: string
  page: number
  scope: 'page' | 'selection'
  context: SlideSource
  /** 选中那一段两侧的文字，帮受话人分辨同一句话的哪一处出现（形状见
   *  `markdownQuote.ts` 的 `contextAround`）。只有 `selection` 才带；整页没有这
   *  个说法，那一支是 undefined。 */
  prefix?: string
  suffix?: string
  /** 选中那一段在页上的位置，页宽页高的比例（0..1），和 `PagePin` 同一个坐标系。
   *  只给「攒着的几处在页上画序号」用，不随引用发出去：`usePreviewQuote` 发的时候
   *  只挑它列出来的那几项，多出来的字段进不了请求。量不出来就不带。 */
  x?: number
  y?: number
  /** 这一处在文档的哪一节里，认不出来是空串。
   *
   *  PDF 的文字层里没有「标题」这个结构，这一句是按字号、整行、在正文之上三个条件
   *  认出来的（见 `pageSections.ts`）。认不出来就不写——说错一节比只说页码坏得多。
   *  和 `x`/`y` 一样只给卡上的位置文案用，不随引用发出去。 */
  section?: string
}
/** 页面上的一点。`x`/`y` 是这一页宽高的比例（0..1），不是像素：页面按面板宽度
 *  重画过好几轮，像素坐标只对当时那一版成立，比例对哪一版都成立。 */
export type PagePin = {
  page: number
  x: number
  y: number
  context: SlideSource
}

/** 页宽页高的比例，夹在 0..1。
 *
 *  分页文档和幻灯片两档量同一个东西（页上文字层的一块，相对这一页的比例），落点在拖动、
 *  缩放下可能差出边界一点点：比例越界会让标记画到页外。两档共用这一处，一个边界改了
 *  另一个跟着改。 */
export function pageRatio(value: number): number {
  return Math.min(1, Math.max(0, value))
}
