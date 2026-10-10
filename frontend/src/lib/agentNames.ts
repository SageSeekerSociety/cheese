import type { DeviceScreen, ProjectMemberRow, TopicMemberRow } from '../cx_types'

import { t } from '../i18n'

/**
 * 一个队友在这块屏幕上叫什么。还用着出生时那个名字的队友（`name_source` 为
 * `default`）库里存的是「芝士」，因为 agent 和提示词读的是它；屏幕按读者的语言叫它，
 * 直到有人给它改名——和没起名的房间（`topicTitle`）是同一个规矩。
 */
export function teammateName(name: string | null | undefined, source: string | null | undefined): string {
  return source === 'default' ? t('work.room.defaultAgentName') : name || ''
}

/** 设备上一块屏幕背后的队友叫什么；屏幕不属于任何房间、没有名字时退回它的 handle。 */
export function screenAgentName(screen: DeviceScreen): string {
  return teammateName(screen.agent_name, screen.agent_name_source) || screen.agent_handle
}

/** 名册上一行的名字：人照原样，队友见 `teammateName`。 */
export function memberName(row: { name?: string | null; name_source?: string | null } | null | undefined): string {
  return row ? teammateName(row.name, row.name_source) : ''
}

/**
 * 名册上一行名字下面那串 handle。队友写它自己的 handle（`cheese`）：座位 handle
 * （`cheese-<hex>`）是内部的编号，没人会打它。项目成员页、频道名册、往频道里加人的
 * 下拉都按这一条写，同一位队友在哪里都是同一串。
 */
export function shownHandle(handle: string, row: { instance_handle?: string | null }): string {
  return row.instance_handle || handle
}

/** 名册上一行队友的两个答案：它叫什么，和它是哪一位。 */
interface AgentSeat {
  name: string
  /** 同一位队友的每个 handle 都指到这一个：它名册上的座位 handle。 */
  identity: string
}

/**
 * AI 队友的 handle → 它叫什么、是哪一位。一个队友有两个 handle：坐在名册上的座位
 * （`cheese-<hex>`，块署名、@ 用它），和它自己的 handle（`cheese`、`cheese-kimi`，
 * 会话、轮次帧、消息的收件人用它）。两个都认，且都归到同一位——同一个人在记录里
 * 用了两个 handle，不是两个人。房间名册先说（房间那一行的名字是房间现在交给的那
 * 位），项目名册补上不在这间房里的队友和队友自己的 handle。
 *
 * 名字和身份由这一处一起产出：各建一张表，迟早有一张忘了改。
 */
function agentSeats(room: TopicMemberRow[], project: ProjectMemberRow[]): Map<string, AgentSeat> {
  const seats = new Map<string, AgentSeat>()
  for (const row of project) {
    if (!row.agent) continue
    const seat: AgentSeat = { name: memberName(row), identity: row.user_handle }
    seats.set(row.user_handle, seat)
    if (row.instance_handle) seats.set(row.instance_handle, seat)
  }
  for (const row of room) {
    if (row.agent) seats.set(row.member_handle, { name: memberName(row), identity: row.member_handle })
  }
  return seats
}

/**
 * AI 队友的 handle → 它叫什么。界面上给队友署名的地方都从这里取，不各查各的。
 * 都认不出的 handle 不在表里，兜底由调用方决定。
 */
export function agentNames(room: TopicMemberRow[], project: ProjectMemberRow[]): Map<string, string> {
  const names = new Map<string, string>()
  for (const [handle, seat] of agentSeats(room, project)) {
    if (seat.name) names.set(handle, seat.name)
  }
  return names
}

/**
 * AI 队友的 handle → 它是哪一位：同一个队友的每个 handle 都映到它座位那个 handle。
 * 「同一位只画一次」按这张表认人，不按 handle 比字符串——一位队友在一处挂的是座位、
 * 另一处挂的是它自己的 handle，比字符串就会把它画成两个人。
 */
export function agentIdentities(room: TopicMemberRow[], project: ProjectMemberRow[]): Map<string, string> {
  const identities = new Map<string, string>()
  for (const [handle, seat] of agentSeats(room, project)) identities.set(handle, seat.identity)
  return identities
}
