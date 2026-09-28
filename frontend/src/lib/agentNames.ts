import type { ProjectMemberRow, TopicMemberRow } from '../cx_types'

/**
 * AI 队友的 handle → 它叫什么。界面上给队友署名的地方都从这里取，不各查各的。
 *
 * 一个队友有两个 handle：坐在名册上的座位（`cheese-<hex>`，块署名、@ 用它），和它
 * 自己的 handle（`cheese-kimi`，会话、轮次帧、消息的收件人用它）。两个都认。房间
 * 名册先说（房间那一行的名字是房间现在交给的那位），项目名册补上不在这间房里的队
 * 友和队友自己的 handle。都认不出的 handle 不在表里，兜底由调用方决定。
 */
export function agentNames(room: TopicMemberRow[], project: ProjectMemberRow[]): Map<string, string> {
  const names = new Map<string, string>()
  for (const row of project) {
    if (!row.agent || !row.name) continue
    names.set(row.user_handle, row.name)
    if (row.instance_handle) names.set(row.instance_handle, row.name)
  }
  for (const row of room) {
    if (row.agent && row.name) names.set(row.member_handle, row.name)
  }
  return names
}
