// 芝士答题时写下的改动（`/assistant/changes`）：草稿的几格、文档的几段。撤销是以人自己
// 的身份写回去，草稿回到这一轮写之前的样子，文档的几段换回原文；恢复是再写成芝士留下的
// 样子。每次都返回这一轮里被牵动的几次改动，各自现在撤销了哪几项。

import { request } from './http'

/** 一次改动现在的样子：哪种东西（`draft` / `doc`）、改了什么，以及已撤销的几项。 */
export interface AgentChange {
  id: number
  kind: string
  undone: string[]
  [key: string]: unknown
}

export interface AgentChangeUndone {
  changes: AgentChange[]
  /** 草稿：放回去的几格（表单的字段名）和草稿现在的版本。 */
  draft?: number | null
  version?: number | null
  fields?: Record<string, unknown> | null
  /** 文档：写回的那份文档。 */
  document?: string | null
}

const post = (body: unknown): RequestInit => ({ method: 'POST', body: JSON.stringify(body) })

export namespace AgentChangesApi {
  /** 撤销一次改动的一项（`part`，草稿是芝士的字段名）；不给就是还没撤销的全部。 */
  export const undo = (id: number, part?: string) =>
    request<AgentChangeUndone>(`/assistant/changes/${id}/undo`, post(part ? { part } : {}))

  export const redo = (id: number, part?: string) =>
    request<AgentChangeUndone>(`/assistant/changes/${id}/redo`, post(part ? { part } : {}))
}
