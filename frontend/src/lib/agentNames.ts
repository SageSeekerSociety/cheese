import type { ProjectMemberRow, TopicMemberRow } from '../cx_types'

import { t } from '../i18n'

/**
 * 一个队友在这块屏幕上叫什么。还用着出生时那个名字的队友（`name_source` 为
 * `default`）库里存的是「芝士」，因为 agent 和提示词读的是它；屏幕按读者的语言叫它，
 * 直到有人给它改名——和没起名的房间（`topicTitle`）是同一个规矩。
 */
export function teammateName(name: string | null | undefined, source: string | null | undefined): string {
  return source === 'default' ? t('work.room.defaultAgentName') : name || ''
}

/** 名册上一行的名字：人照原样，队友见 `teammateName`。 */
export function memberName(row: { name?: string | null; name_source?: string | null } | null | undefined): string {
  return row ? teammateName(row.name, row.name_source) : ''
}

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
    const name = memberName(row)
    if (!row.agent || !name) continue
    names.set(row.user_handle, name)
    if (row.instance_handle) names.set(row.instance_handle, name)
  }
  for (const row of room) {
    const name = memberName(row)
    if (row.agent && name) names.set(row.member_handle, name)
  }
  return names
}
