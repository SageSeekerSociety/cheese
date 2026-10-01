import type { FileSource } from '@/cx_types'

/** File identity supplied by the owning authorized byte reader, not inferred from pixels. */
export type SlideSource = {
  topicId: string
  path: string
  source: FileSource
  taskId?: string | null
  version: string
}
export type SlidePageContext = {
  text: string
  page: number
  scope: 'page'
  context: SlideSource
}
