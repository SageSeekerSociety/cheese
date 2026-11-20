// 题目草稿（`/task-drafts`）：发布页、改题页边写边存，发布时由服务端照草稿发出去。
// 形状见 `@/types/taskDrafts`。
import type { AxiosProgressEvent } from 'axios'
import type { Task, TaskDraft, TaskDraftFields, TaskDraftSummary } from '@/types'

import { axiosRequest } from './axiosHttp'
import { request } from './http'

const json = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body) })

export namespace TaskDraftsApi {
  /** 我在这个空间的草稿，最近改过的在前。 */
  export const list = (spaceId: number) =>
    request<{ drafts: TaskDraftSummary[] }>(`/task-drafts?space=${spaceId}`).then((data) => data.drafts)

  /** 新建一份草稿。 */
  export const start = (spaceId: number, fields: TaskDraftFields = {}) =>
    request<TaskDraft>('/task-drafts', json('POST', { space: spaceId, fields }))

  /** 一道已发布题目的修改：已有的那份，或者从题目现在的样子开一份。 */
  export const forTask = (taskId: number) => request<TaskDraft>('/task-drafts', json('POST', { task: taskId }))

  /** 打开改题页：已有的那份修改（没有是 null），和表单的起点。只读，不开新的。 */
  export const changes = (taskId: number) =>
    request<{ draft: TaskDraft | null; form: TaskDraftFields }>(`/task-drafts/changes?task=${taskId}`)

  export const read = (id: number) => request<TaskDraft>(`/task-drafts/${id}`)

  /** 存下人在页面上改的这几格。 */
  export const save = (id: number, fields: TaskDraftFields) =>
    request<{ version: number; updatedAt: string }>(`/task-drafts/${id}`, json('PATCH', { fields }))

  export const remove = (id: number) => request<Record<string, never>>(`/task-drafts/${id}`, { method: 'DELETE' })

  /** 发布页上刚选的起始代码：服务端先查能不能作为起始代码，合格才存成附件、交回 id；
   *  不合格时说为什么，什么都不存。转发浏览器的上传进度。 */
  export const uploadStarter = (file: File, onProgress?: (event: AxiosProgressEvent) => void) => {
    const data = new FormData()
    data.append('file', file)
    return axiosRequest<{ id: number }>({
      url: '/task-drafts/starter',
      method: 'POST',
      data,
      timeout: 60000,
      onUploadProgress: onProgress,
    })
  }

  /** 发布：新题目发出去，或把修改保存到已发布的题目上。草稿随之删除。 */
  export const publish = (id: number) =>
    request<{ task: Task; draftId: number }>(`/task-drafts/${id}/publish`, { method: 'POST' })
}
