/**
 * 「我的工作」页的算术：分组、排序、以及每张卡上那句「最近在发生什么」。
 *
 * 全是纯函数，没有一处自己发请求，也没有一处自己算「谁在跑」：`running` /
 * `awaits_me` / `last_activity_at` 三个字段都是服务端算好的（见
 * `Topic` 上那几段注释），这里只做选中、计数、排序。前端再算一遍的后果是同一个
 * 房间里「有没有在跑」会有两个答案，而屏幕上那个是哪一个算出来的，谁也说不清。
 */
import type { Project, Topic, WaitingItem } from '@/cx_types'
import type { Shell } from '@/lib/shell'

import { relTime } from '@/lib/relTime'
import { shellOf } from '@/lib/shell'

/** 一个项目「最近在发生什么」的全部原料。 */
export interface WorkSignal {
  /** 正在跑的房间数。 */
  running: number
  /** 点到我、还没处理完的事（`/awaiting-me`）。 */
  awaiting: number
  /** 最后活动时间；一条消息都没有过就是 null。 */
  lastActivityAt: string | null
}

/** 还没问到 / 问不到时的答复：不假装有动静。 */
export const NO_SIGNAL: WorkSignal = { running: 0, awaiting: 0, lastActivityAt: null }

/** 项目卡片上的「所属空间」。只有从赛题建出来的项目才有。 */
export interface SpaceRef {
  id: number
  name: string
}

/** 一张项目卡片：项目本身、它的动静、它的来路。 */
export interface WorkCard {
  project: Project
  signal: WorkSignal
  space: SpaceRef | null
}

/** 一组卡片 = 一个壳。分组只看壳，不看身份、不看归属。 */
export interface WorkGroup {
  /** 壳的名字，也是这一组的身份（`CATALOG` 按名字索引，所以它是唯一的）。 */
  key: string
  shell: Shell
  cards: WorkCard[]
}

/** 这个项目的房间现在是什么样——`running` 和「最后动过没有」都从这一份里数。 */
export function topicsSignal(topics: readonly Topic[]): { running: number; lastActivityAt: string | null } {
  let running = 0
  let last = 0
  let lastIso: string | null = null
  for (const topic of topics) {
    if (topic.running) running += 1
    // 按值比大小，不按位置：`listTopics` 今天带 sort，但这份函数不该依赖调用方
    // 排没排序——它答的是「最新的那条」，不是「第一条」。
    const at = topic.last_activity_at ? new Date(topic.last_activity_at).getTime() : NaN
    if (!Number.isNaN(at) && at > last) {
      last = at
      lastIso = topic.last_activity_at ?? null
    }
  }
  return { running, lastActivityAt: lastIso }
}

/** 每个项目点到我几件事：`/awaiting-me` 是**一次**跨项目的请求，所以在这里按项目分堆。 */
export function awaitingByProject(items: readonly WaitingItem[]): Record<string, number> {
  const counts: Record<string, number> = {}
  for (const item of items) counts[item.projectId] = (counts[item.projectId] ?? 0) + 1
  return counts
}

/**
 * 排序用的时间戳。**问不出来的一律排最后**，不是排最前：清单还没到货时每个项目
 * 都是未知，谁先谁后无所谓；而清单到了之后，一个「还没开动过」的项目不该压在一个
 * 三分钟前刚有动静的项目上面。
 */
export function activityStamp(signal: WorkSignal): number {
  if (!signal.lastActivityAt) return Number.NEGATIVE_INFINITY
  const at = new Date(signal.lastActivityAt).getTime()
  return Number.isNaN(at) ? Number.NEGATIVE_INFINITY : at
}

/** 最近动过的在前。`sort` 是稳定的，所以并列的保持清单本来的顺序，不随机洗牌。 */
export function sortCards(cards: readonly WorkCard[]): WorkCard[] {
  return [...cards].sort((a, b) => {
    const x = activityStamp(a.signal)
    const y = activityStamp(b.signal)
    return x === y ? 0 : y - x
  })
}

/**
 * 卡片按壳分组。
 *
 * **顺序不是一张写死的壳清单**：组跟着它最近动过的那张卡走，所以今天在用的那个壳
 * 就排在前面，而不是「课程永远第一」。这样服务端加第五个壳时，这一页不需要发版
 * ——多出来的那些项目自己会成一个新组（组名读这个壳的词表）。
 */
export function workGroups(cards: readonly WorkCard[]): WorkGroup[] {
  const groups: WorkGroup[] = []
  for (const card of sortCards(cards)) {
    const shell = shellOf(card.project)
    const found = groups.find((group) => group.key === shell.name)
    if (found) found.cards.push(card)
    else groups.push({ key: shell.name, shell, cards: [card] })
  }
  return groups
}

/** 「我加入的空间」= 我的项目所在的那几个空间，按卡片顺序去重。 */
export function joinedSpaces(cards: readonly WorkCard[]): SpaceRef[] {
  const seen = new Map<number, SpaceRef>()
  for (const card of cards) {
    if (card.space && !seen.has(card.space.id)) seen.set(card.space.id, card.space)
  }
  return [...seen.values()]
}

/**
 * 卡片上那一行「最近在发生什么」。
 *
 * 返回的是**零件**而不是一句话：词要过 i18n（词表还会换词），而这个模块不碰 i18n。
 * 顺序就是读的顺序——什么在跑、什么在等我、最近什么时候动过。
 *
 * 三样都答不出来时说「还没开动」，不留空行：空白和「没加载出来」长得一模一样。
 */
export type ActivityPart =
  | { kind: 'running'; count: number }
  | { kind: 'awaiting'; count: number }
  | { kind: 'activity'; time: string }
  | { kind: 'quiet' }

export function activityParts(signal: WorkSignal): ActivityPart[] {
  const parts: ActivityPart[] = []
  if (signal.running > 0) parts.push({ kind: 'running', count: signal.running })
  if (signal.awaiting > 0) parts.push({ kind: 'awaiting', count: signal.awaiting })
  const time = relTime(signal.lastActivityAt)
  if (time) parts.push({ kind: 'activity', time })
  return parts.length > 0 ? parts : [{ kind: 'quiet' }]
}
