import type { CreateKnowledgeRequest, Knowledge, ListKnowledgesParams, Page, UpdateKnowledgeRequest } from '@/types'

import { axiosMutation, axiosRequest } from './axiosHttp'

/** Knowledges ride the new API origin; JSON calls expose only the payload.
 *
 *  列表接口的参数照旧自己拼 `URLSearchParams`：`undefined` 的键**省略**（不是写空串），
 *  数组按下标重复 append 同名键（同旧模块），所以重复标签、排序都对得上。 */
export namespace KnowledgesApi {
  export const create = (data: CreateKnowledgeRequest) =>
    axiosRequest<{ knowledge: Knowledge }>({ url: '/knowledge', method: 'POST', data }, true)

  export const list = (params: ListKnowledgesParams) => {
    const finalParams = new URLSearchParams()
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined) {
        if (Array.isArray(value)) {
          value.forEach((v) => finalParams.append(key, v.toString()))
        } else {
          finalParams.set(key, value.toString())
        }
      }
    })
    return axiosRequest<{ knowledges: Knowledge[]; page: Page }>(
      { url: '/knowledge', method: 'GET', params: finalParams },
      true
    )
  }

  export const getDetail = (knowledgeId: number) =>
    axiosRequest<{ knowledge: Knowledge }>({ url: `/knowledge/${knowledgeId}`, method: 'GET' }, true)

  export const update = (knowledgeId: number, data: UpdateKnowledgeRequest) =>
    axiosRequest<{ knowledge: Knowledge }>({ url: `/knowledge/${knowledgeId}`, method: 'PATCH', data }, true)

  export const deleteKnowledge = (knowledgeId: number) =>
    axiosMutation({ url: `/knowledge/${knowledgeId}`, method: 'DELETE' }, true)
}
