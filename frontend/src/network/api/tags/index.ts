import type { CreateTagResponse, SearchTagsResponse } from './types'

import ApiInstance from '../index'

// 知是 标签 — served at `/topics` until #370 moved it to `/tags`. The response
// keys stay `topics`: that is what the 知是 UI calls them (话题), and product
// language is not a refactor's to change. Only the address moved.
export namespace TagsApi {
  export const search = (query: string, pageSize = 20) =>
    ApiInstance.request<SearchTagsResponse>({
      url: '/tags',
      method: 'GET',
      params: { q: query, pageSize: pageSize },
    })

  export const create = (name: string) =>
    ApiInstance.request<CreateTagResponse>({
      url: '/tags',
      method: 'POST',
      data: { name },
    })
}
