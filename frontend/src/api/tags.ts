import type { Page, Topic } from '@/types'

import { axiosRequest } from './axiosHttp'

// 知是 标签 — 老 origin（primary）。搜索与新建都只交出 payload：调用方拿到的就是
// `{ topics, page }` / `{ id }`，不是整只信封。地址仍叫 `/tags`（#370 从 `/topics`
// 搬来），返回的键仍叫 `topics`——那是知是 UI 对「话题」的称呼，产品语言不归重构改。
export namespace TagsApi {
  export type SearchTagsResponse = {
    topics: Topic[]
    page: Page
  }

  export type CreateTagResponse = {
    id: number
  }

  export const search = (query: string, pageSize = 20) =>
    axiosRequest<SearchTagsResponse>({ url: '/tags', method: 'GET', params: { q: query, pageSize } })

  export const create = (name: string) =>
    axiosRequest<CreateTagResponse>({ url: '/tags', method: 'POST', data: { name } })
}
