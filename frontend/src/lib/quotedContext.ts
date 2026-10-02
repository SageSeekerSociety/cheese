import type { FileSource } from '../cx_types'

/** Original source data; never expanded or passed through mention rendering. */
export type QuotedContext = Readonly<{
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

export function frozenQuote(quote: QuotedContext): QuotedContext {
  return Object.freeze({ ...quote })
}

export function isQuotedContext(value: unknown): value is QuotedContext {
  if (!value || typeof value !== 'object') return false
  const q = value as Partial<QuotedContext>
  return (
    q.kind === 'slide-page' &&
    typeof q.path === 'string' &&
    (q.source === 'live' || q.source === 'committed') &&
    typeof q.version === 'string' &&
    (q.task_id === null || typeof q.task_id === 'string') &&
    Number.isInteger(q.page) &&
    (q.page ?? 0) > 0 &&
    (q.scope === undefined || q.scope === 'page' || q.scope === 'selection') &&
    typeof q.text === 'string'
  )
}
