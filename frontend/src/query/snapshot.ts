// 进一个房间时，房间连接上的 `subscribed` 帧带着这个房间此刻的样子（`room`）：名册、
// 任务、置顶、支线、提议卡；任务还有它自己、相关、审阅意见。每一块和它自己那个接口
// 回的一模一样（后端 `room_snapshot.py`），这里把它们拆开放进各自那一格缓存。
//
// 页面上的组件一挂上就要读这几格，往往比 `subscribed` 回来得早。所以打开房间的导航
// 一出发就记下「这个房间的快照要来」（`expectRoom`），这几格的读先等它：快照到了就
// 用快照里那一块，没到（连接被拒、超时）再自己问。重连回来再订阅一次，又是一份新的
// 快照，在缓存里的那几格直接盖上。
//
// 不在缓存里的几块（提议卡、任务的相关和审阅意见，由各自的组合函数读）只交给快照到
// 之前就在等的那一次读：快照之后才发起的读，可能是一帧「变了」叫起来的，那时快照
// 已经旧了，得照常问服务器。
import type { ProjectSkill } from '@/api/projectSkills'
import type { FeedbackProposal, ListPayload, RoomTask, TopicMemberRow } from '@/cx_types'
import type { ChannelPin } from '@/types/channels'
import type { ReviewComment } from '@/types/reviewComment'
import type { TaskRelated } from '@/types/taskOrigin'
import type { ThreadRow } from '@/types/threads'

import { hashKey } from '@tanstack/vue-query'

import { RECENT_DONE } from '@/lib/channelTasks'
import { queryClient } from '@/query/client'
import { keys } from '@/query/keys'

export interface RoomSnapshot {
  members: TopicMemberRow[]
  feedback_proposals: FeedbackProposal[]
  skill_proposals: ProjectSkill[]
  /** 频道才有。 */
  tasks?: { open: RoomTask[]; recent: RoomTask[] }
  pins?: ChannelPin[]
  threads?: ThreadRow[]
  /** 任务才有。 */
  task?: RoomTask
  related?: TaskRelated
  review_comments?: { comments: ReviewComment[] }
}

/** 快照最多等这么久：再久就各自去问，不让房间一直空着。 */
const WAIT_MS = 2_500

const page = <T>(rows: T[]): ListPayload<T> => ({ data: rows, total: rows.length })

/** 房间 `room` 里的对话 `conversation`（频道就是它自己，任务是任务的 id）的快照里有哪几格。 */
function pieces(conversation: string, room: string, snapshot: RoomSnapshot | null) {
  const task = conversation !== room
  const cached: [readonly unknown[], unknown][] = [[keys.roomMembers(room), snapshot && page(snapshot.members)]]
  if (task) {
    cached.push([keys.roomTask(conversation), snapshot?.task])
  } else {
    cached.push(
      [keys.roomTaskList(room, { limit: 0, status: 'open' }), snapshot?.tasks && page(snapshot.tasks.open)],
      [
        keys.roomTaskList(room, { limit: 0, status: 'closed', latest: RECENT_DONE }),
        snapshot?.tasks && page(snapshot.tasks.recent),
      ],
      [keys.roomPins(room), snapshot?.pins],
      [keys.roomThreads(room), snapshot?.threads]
    )
  }
  const waited: [readonly unknown[], unknown][] = [
    [keys.roomFeedbackProposals(conversation), snapshot?.feedback_proposals],
    [keys.roomSkillProposals(room), snapshot?.skill_proposals],
  ]
  if (task) {
    waited.push(
      [keys.taskRelated(conversation), snapshot?.related],
      [keys.taskReviewComments(conversation), snapshot?.review_comments]
    )
  }
  return { cached, waited }
}

interface Waiting {
  promise: Promise<unknown>
  resolve: (piece: unknown) => void
}
const waiting = new Map<string, Waiting>()

function wait(hash: string): void {
  if (waiting.has(hash)) return
  let resolve!: (piece: unknown) => void
  const promise = new Promise<unknown>((done) => (resolve = done))
  const entry = { promise, resolve }
  waiting.set(hash, entry)
  setTimeout(() => {
    if (waiting.get(hash) !== entry) return
    waiting.delete(hash)
    resolve(undefined)
  }, WAIT_MS)
}

/** 打开这个房间的导航出发了：它的快照会随订阅一起来，这几格的读先等它。 */
export function expectRoom(conversation: string, room: string): void {
  const { cached, waited } = pieces(conversation, room, null)
  for (const [key] of [...cached, ...waited]) wait(hashKey(key))
}

/** 订阅生效了：快照里每一块放进它那一格，等着的读拿到它；没带快照就让它们各自去问。 */
export function settleRoom(conversation: string, room: string, snapshot: RoomSnapshot | null | undefined): void {
  const { cached, waited } = pieces(conversation, room, snapshot ?? null)
  for (const [key, piece] of cached) {
    if (piece !== undefined && piece !== null) queryClient.setQueryData(key, piece)
  }
  for (const [key, piece] of [...cached, ...waited]) {
    const hash = hashKey(key)
    const entry = waiting.get(hash)
    if (!entry) continue
    waiting.delete(hash)
    entry.resolve(piece ?? undefined)
  }
}

/**
 * 这一格的读：快照要来就先等它，快照里有就用那一块，没有再 `read()`。快照之后的每一次
 * 读（推送说变了、重试）照常问服务器。
 */
export async function fromSnapshot<T>(queryKey: readonly unknown[], read: () => Promise<T>): Promise<T> {
  const expected = waiting.get(hashKey(queryKey))
  const piece = expected ? await expected.promise : undefined
  return piece !== undefined ? (piece as T) : read()
}

/** 换人登录、退出，或者测试之间：上一段会话还在等的读都不等了，各自去问。 */
export function forgetRoomSnapshots(): void {
  for (const entry of waiting.values()) entry.resolve(undefined)
  waiting.clear()
}
