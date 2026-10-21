// 芝士替人起草、等人确认的消息和任务（`/assistant/proposals`）。发出去走人自己发消息、开
// 任务的那几条路；这里只记下它发出了（和发出的是哪条、哪个任务），或者人说不用了。

import { request } from './http'

export interface AgentProposalSettled {
  id: number
  kind: string
  state: 'sent' | 'dismissed'
  result?: Record<string, string> | null
}

export namespace AgentProposalsApi {
  export const settle = (id: number, state: 'sent' | 'dismissed', result?: Record<string, string>) =>
    request<AgentProposalSettled>(`/assistant/proposals/${id}`, {
      method: 'POST',
      body: JSON.stringify(result ? { state, result } : { state }),
    })
}
