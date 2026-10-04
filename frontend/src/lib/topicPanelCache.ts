// 工作面板那几份「每个话题一份」的数据：进度清单、话题成员、房间派出去的活。
//
// 实测切进一个话题时，这几份各要 400–700ms，面板在这段时间里是空的或在转圈，而人
// 刚刚才看过它们。这里用的是 `blockCache.ts` / `pageCache.ts` 已经验证过的那一招：
// **先把上次的画出来，再在背后重取**。
//
// - 只活在这个标签页的内存里，不落 storage。
// - 按话题记，最多 MAX_TOPICS 个话题；超出时最久没碰过的那个话题整份丢掉。
// - 同一个话题、同一种数据同一时刻只发一个请求：面板、对话栏、改动页在同一秒里
//   都要「这个房间的活」，它们要的是同一样东西。
// - 失败不写缓存，也不吞掉错误：调用方照旧决定失败时显示什么。
// - 退出登录时清空（`services/account.ts`），连正在飞的请求一起作废。
import type { Block, ListPayload, RoomTask, TopicMemberRow, TopicProgress } from '../cx_types'

import { getProgress, listRoomTasks, listTopicMembers } from '../api'

import { onTopicRosterChange } from './topicRosterChanges'

export interface TopicPanelData {
  progress: TopicProgress
  members: ListPayload<TopicMemberRow>
  /** `listRoomTasks(id, { limit: 1 })`：每条活只带最新那一块。 */
  roomTasks: ListPayload<RoomTask & { blocks: Block[] }>
}
export type TopicPanelKind = keyof TopicPanelData

// 「最近来回切的那些房间」：20 个足够覆盖，而一个房间三份数据不大。
const MAX_TOPICS = 20

const entries = new Map<string, Partial<TopicPanelData>>()
const inflight = new Map<string, Promise<unknown>>()
// 清空一次 +1：那一刻还在飞的请求回来后认得出自己属于上一个身份，不写回来。
let generation = 0

function touch(topicId: string): Partial<TopicPanelData> {
  const held = entries.get(topicId) ?? {}
  // 先删再写，让它排到最新：Map 按首次插入的顺序迭代，队头就是最久没碰过的。
  entries.delete(topicId)
  entries.set(topicId, held)
  while (entries.size > MAX_TOPICS) {
    const oldest = entries.keys().next()
    if (oldest.done) break
    entries.delete(oldest.value)
  }
  return held
}

/** 上次取到的那一份；没取过是 undefined。只读，不改先后顺序。 */
export function cachedTopicPanel<K extends TopicPanelKind>(kind: K, topicId: string): TopicPanelData[K] | undefined {
  return entries.get(topicId)?.[kind]
}

/** 取一次并写进缓存；同一个话题同一种数据复用正在飞的那一条。 */
export function fetchTopicPanel<K extends TopicPanelKind>(
  kind: K,
  topicId: string,
  fetcher: () => Promise<TopicPanelData[K]>
): Promise<TopicPanelData[K]> {
  const key = `${kind}:${topicId}`
  const running = inflight.get(key) as Promise<TopicPanelData[K]> | undefined
  if (running) return running
  const startedAt = generation
  const tracked: Promise<TopicPanelData[K]> = fetcher()
    .then((value) => {
      // 已经被作废（名册刚改过、或者退出登录）就不写：后发的那条才是现在的样子。
      if (startedAt === generation && inflight.get(key) === tracked) touch(topicId)[kind] = value
      return value
    })
    .finally(() => {
      if (inflight.get(key) === tracked) inflight.delete(key)
    })
  inflight.set(key, tracked)
  return tracked
}

export function fetchTopicProgress(topicId: string): Promise<TopicProgress> {
  return fetchTopicPanel('progress', topicId, () => getProgress(topicId))
}

export function fetchTopicMembers(topicId: string): Promise<ListPayload<TopicMemberRow>> {
  return fetchTopicPanel('members', topicId, () => listTopicMembers(topicId))
}

// 这个页面刚改过某个话题的名册：正在飞的那条名册请求是改之前发出去的，作废它，
// 下一位来要的人会另发一条，之后的人再跟着那一条走。这个监听在模块加载时注册，
// 排在各个组件的重拉监听前面，所以它们重拉时拿到的已经是新的那一条。
onTopicRosterChange((topicId) => {
  inflight.delete(`members:${topicId}`)
})

export function fetchRoomTasks(topicId: string): Promise<TopicPanelData['roomTasks']> {
  return fetchTopicPanel('roomTasks', topicId, () => listRoomTasks(topicId, { limit: 1 }))
}

/** 退出登录时调用：上一个人的房间数据不能留给下一个人。测试之间也用它擦干净。 */
export function clearTopicPanelCache(): void {
  generation += 1
  entries.clear()
  inflight.clear()
}
