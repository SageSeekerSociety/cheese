// 私聊的地址：一个字符串同时是 URL 里那一段、未读表的键，和「我在跟谁说话」。
//
// 一个人用自己的 handle，一个 AI 队友用 `agent:<handle>`。队友的 handle 是每个
// 项目自己起的（AgentInstance.handle），没有任何东西拦着谁把队友命名成名册上某个
// 人的 handle —— 不加前缀，那个人的私聊和那个队友的私聊就是同一个字符串，未读会
// 记到错的行上，地址栏也分不出点开的是谁。后端 `agent_dm_key` 生成的是同一个词。

export const AGENT_DM_PREFIX = 'agent:'

/** How the UI addresses the DM with this AI teammate. */
export function agentDmKey(agentHandle: string): string {
  return `${AGENT_DM_PREFIX}${agentHandle}`
}

/** The teammate a DM address names, or null when it names a person. */
export function agentHandleOf(peer: string): string | null {
  return peer.startsWith(AGENT_DM_PREFIX) ? peer.slice(AGENT_DM_PREFIX.length) : null
}
