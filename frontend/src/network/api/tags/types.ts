import type { Page, Topic } from '@/types'

// `Topic` here is the 知是 类型 for a 标签 (id + name) — the shared frontend type
// still carries the product's word. See ./index.ts on why the keys did not move.
export type SearchTagsResponse = {
  topics: Topic[]
  page: Page
}

export type CreateTagResponse = {
  id: number
}
