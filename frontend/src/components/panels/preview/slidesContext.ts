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
}
/** 页面上的一点。`x`/`y` 是这一页宽高的比例（0..1），不是像素：页面按面板宽度
 *  重画过好几轮，像素坐标只对当时那一版成立，比例对哪一版都成立。 */
export type PagePin = {
  page: number
  x: number
  y: number
  context: SlideSource
}
