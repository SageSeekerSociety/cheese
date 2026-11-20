// 进一个房间时，房间连接上的 `subscribed` 帧带着这个房间此刻的样子（`room`）：名册、
// 任务、置顶、支线、提议卡；任务还有它自己、相关、审阅意见。每一块和它自己那个接口
// 回的一模一样（后端 `room_snapshot.py`），这里把它们拆开放进各自那一格缓存。
//
// 页面上的组件一挂上就要读这几格，往往比 `subscribed` 回来得早。所以打开房间的导航
// 一出发就记下「这个房间的快照要来」（`expectRoom`），这几格的读先等它：快照到了就
// 用快照里那一块，没到（连接被拒、超时）再自己问。重连回来再订阅一次，又是一份新的
// 快照，在缓存里的那几格直接盖上。
//
// 不经 `useQuery` 的几块（提议卡、任务的相关和审阅意见，由各自的组合函数读）只交给
// 快照到之前就在等的那一次读：快照之后才发起的读，可能是一帧「变了」叫起来的，那时快照
// 已经旧了，得照常问服务器。它们上一次在这里的样子另记在缓存里（`lastSeen` / `seen`）：
// 再进这个房间时消息是从缓存里直接画的，这几块要是从空开始等订阅一来一回，就会晚一拍
// 才冒出来，把已经画好的对话顶一下。
import type { ProjectSkill } from '@/api/projectSkills'
import type { FeedbackProposal, ListPayload, RoomTask, TopicMemberRow } from '@/cx_types'
import type { ChannelPin } from '@/types/channels'
import type { ChapterRow } from '@/types/chapters'
import type { ReviewComment } from '@/types/reviewComment'
import type { TaskRelated } from '@/types/taskOrigin'
import type { ThreadLists } from '@/types/threads'

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
  threads?: ThreadLists
  /** 这段对话的章节：频道和任务都有。 */
  chapters?: ChapterRow[]
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
  // Chapters belong to the conversation being read, so both a room's own page
  // and a task's page find them on the same key.
  cached.push([keys.roomChapters(conversation), snapshot?.chapters])
  const waited: [readonly unknown[], unknown][] = [
    [keys.roomFeedbackProposals(conversation), snapshot?.feedback_proposals],
    [keys.roomSkillProposals(conversation), snapshot?.skill_proposals],
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
  const head = early.get(hashKey(queryKey))
  if (head) {
    early.delete(hashKey(queryKey))
    if (Date.now() - head.at < EARLY_MS) return head.read as Promise<T>
  }
  const expected = waiting.get(hashKey(queryKey))
  const piece = expected ? await expected.promise : undefined
  return piece !== undefined ? (piece as T) : read()
}

/** 换人登录、退出，或者测试之间：上一段会话还在等的读都不等了，各自去问。 */
export function forgetRoomSnapshots(): void {
  for (const entry of waiting.values()) entry.resolve(undefined)
  waiting.clear()
  early.clear()
}

/** 先发出去的读多久之内还算数：再晚接手，它说的就是打开页面那一刻的事了。 */
const EARLY_MS = 15_000
const early = new Map<string, { at: number; read: Promise<unknown> }>()

/**
 * 冷打开（整页刷新、直接打开链接）时这一块先去问：订阅要等房间页挂上才连，快照赶不上，
 * 等它就要比消息晚一个来回。读回来就记成 `lastSeen`，挂上来的第一次 `fromSnapshot`
 * 直接接着这一次，不另发。
 */
export function readEarly<T>(queryKey: readonly unknown[], read: () => Promise<T>): void {
  const pending = read().then((value) => {
    seen(queryKey, value)
    return value
  })
  pending.catch(() => {})
  early.set(hashKey(queryKey), { at: Date.now(), read: pending })
}

/**
 * 这一块上一次在这里的样子（快照、服务器回来的，或者在这里改过之后的）：再进这个房间先
 * 画它，等着的那一次读回来再换。没看过是 undefined。换人登录时整份缓存清空，它也跟着没。
 */
export function lastSeen<T>(queryKey: readonly unknown[]): T | undefined {
  return queryClient.getQueryData<T>(queryKey)
}

/** 记下这一块此刻的样子，给下一次进这个房间先画。 */
export function seen<T>(queryKey: readonly unknown[], value: T): void {
  queryClient.setQueryData<T>(queryKey, value)
}
