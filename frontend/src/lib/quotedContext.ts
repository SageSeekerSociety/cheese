import type { FileSource } from '../cx_types'

/** Original source data; never expanded or passed through mention rendering. */
export type QuotedContext = Readonly<{
  kind: 'slide-page'
  path: string
  source: FileSource
  version: string
  task_id: string | null
  page: number
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
    typeof q.text === 'string'
  )
}
