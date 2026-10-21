// 芝士替人起草、等人确认的消息和任务（`/assistant/proposals`）。发出去走人自己发消息、开
// 任务的那几条路；这里只记下它发出了（和发出的是哪条、哪个任务），或者人说不用了。

import { request } from './http'

/** 一个 `act` 卡确认后要做的一次调用：人自己页面上调的那条路由。`{prev}` 换成上一次
 *  调用回来的 `id`（先开任务、再往任务里发交代）。 */
export interface AssistantCall {
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  path: string
  body?: unknown
}

export interface AgentProposalSettled {
  id: number
  kind: string
  state: 'sent' | 'dismissed'
  result?: Record<string, string> | null
}

export namespace AgentProposalsApi {
  /** 一个 `act` 卡确认后的一次调用，以这个人的身份；`prev` 是上一次调用回来的 id。 */
  export const run = (call: AssistantCall, prev?: string) =>
    request<{ id?: string } | null>(prev ? call.path.replace('{prev}', prev) : call.path, {
      method: call.method,
      ...(call.body === undefined ? {} : { body: JSON.stringify(call.body) }),
    })

  export const settle = (id: number, state: 'sent' | 'dismissed', result?: Record<string, string>) =>
    request<AgentProposalSettled>(`/assistant/proposals/${id}`, {
      method: 'POST',
      body: JSON.stringify(result ? { state, result } : { state }),
    })
}
