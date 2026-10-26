// 一个房间（频道、任务、私聊）围着对话的那几份：名册、进度、进行中的任务、当前预览、
// 任务房间里那个任务。房间头部、右侧面板、对话栏、@ 候选读的是同一份。
import type { Block, ListPayload, PreviewInfo, RoomTask, TodoItem, TopicMemberRow, TopicProgress } from '@/cx_types'
import type { ChannelPin } from '@/types/channels'
import type { ChapterRow } from '@/types/chapters'
import type { TopicEnvironment } from '@/types/compute'
import type { Thread, ThreadLists } from '@/types/threads'

import { queryOptions } from '@tanstack/vue-query'

import { getPreview, getProgress, getTask, getTopicEnvironment, listRoomTasks, listTopicMembers } from '@/api'
import { listChapters } from '@/api/chapters'
import { listPins } from '@/api/pins'
import { getThread, listThreads } from '@/api/threads'
import { prefetchNewestBlocks } from '@/query/blocks'
import { queryClient, refreshQueries } from '@/query/client'
import { keys } from '@/query/keys'
import { fromSnapshot } from '@/query/snapshot'

/** 房间名册。名册的写法（`api/topicMembers`）写完就把它标过期。 */
export function roomMembersQuery(roomId: string) {
  return queryOptions({
    queryKey: keys.roomMembers(roomId),
    queryFn: (): Promise<ListPayload<TopicMemberRow>> =>
      fromSnapshot(keys.roomMembers(roomId), () => listTopicMembers(roomId)),
  })
}

/** 这段对话新会话默认的环境，和每条会话各自在哪个环境里。 */
export function environmentQuery(roomId: string) {
  return queryOptions({
    queryKey: keys.roomEnvironment(roomId),
    queryFn: (): Promise<TopicEnvironment> => getTopicEnvironment(roomId),
  })
}

/** 频道的置顶：概览里那一块和对话栏里的小图钉读同一份。 */
export function pinsQuery(roomId: string) {
  return queryOptions({
    queryKey: keys.roomPins(roomId),
    queryFn: (): Promise<ChannelPin[]> => fromSnapshot(keys.roomPins(roomId), () => listPins(roomId)),
  })
}

/** 一段对话的章节：概览里那一块和主线里消息的 ⋯ 读同一份。键是那段对话的 id ——
 *  频道是频道本身，任务是任务的 id。 */
export function chaptersQuery(conversationId: string) {
  return queryOptions({
    queryKey: keys.roomChapters(conversationId),
    queryFn: (): Promise<ChapterRow[]> =>
      fromSnapshot(keys.roomChapters(conversationId), () => listChapters(conversationId)),
  })
}

/** 频道的支线，两栏：开着的（最近有回复的在前）和收起来的（最近收的在前）。概览里
 *  那一格和主线消息下面那一行读同一份 —— 后者只用 `open` 那一栏。 */
export function threadsQuery(roomId: string) {
  return queryOptions({
    queryKey: keys.roomThreads(roomId),
    queryFn: (): Promise<ThreadLists> =>
      fromSnapshot(keys.roomThreads(roomId), async () => {
        const lists = await listThreads(roomId, 100)
        return lists && Array.isArray(lists.open) ? lists : { open: [], recycled: [] }
      }),
  })
}

/**
 * 一条支线：它自己，和它挂着的那条主线消息（支线页页头画的就是那条）。
 *
 * 页头那条消息不该等这一次读回来才出现：点开一条支线时，它挂着的那块消息往往已经在手上了
 * （频道那段对话的窗口里，消息带着它自己的支线），先拿它画上（`placeholderData`），读回来
 * 再换成完整的那一份。手上没有（整页打开一条老支线）就只能等，和以前一样。
 */
export function threadQuery(threadId: string) {
  return queryOptions({
    queryKey: keys.roomThread(threadId),
    queryFn: (): Promise<Thread> => getThread(threadId),
    placeholderData: () => listedThread(threadId),
  })
}

/** 缓存里拼得出来的这一条支线：任何一份对话窗口里，那一块消息带着它的支线
 *  （`GET /topics/{id}/blocks` 在一行上带着 `thread`）。窗口里没有那块消息（它早已翻出
 *  最新那一段，从频道概览的支线列表点开），就用列表里那一行带着的开局消息——只在它是
 *  一条完整的消息时：列表里那一句被截过的话是预览，当成整条消息画出来就是说错了。
 *  都没有是 undefined。 */
export function listedThread(threadId: string): Thread | undefined {
  for (const [, data] of queryClient.getQueriesData<unknown>({ predicate: holdsBlocks })) {
    const block = blocksIn(data).find((held) => held.thread?.id === threadId)
    if (block?.thread) {
      return {
        id: block.thread.id,
        room_id: block.thread.room_id,
        root_block_id: block.id,
        reply_count: block.thread.reply_count,
        last_reply_at: block.thread.last_reply_at,
        root: block,
      }
    }
  }
  for (const [, rows] of queryClient.getQueriesData<ThreadRow[]>({ predicate: holdsThreadRows })) {
    const row = Array.isArray(rows) ? rows.find((held) => held.id === threadId) : undefined
    const root = row?.root
    if (!row || !root || !root.author_type || root.kind !== 'message' || root.truncated !== false) continue
    return {
      id: row.id,
      room_id: row.room_id,
      root_block_id: row.root_block_id,
      reply_count: row.reply_count,
      last_reply_at: row.last_reply_at,
      root: {
        id: row.root_block_id,
        conversation_id: row.room_id,
        kind: root.kind,
        author_type: root.author_type,
        author: root.author,
        content: root.content,
        created_at: root.created_at,
        meta: root.checklist ? { checklist: root.checklist } : null,
      } as Block,
    }
  }
  return undefined
}

// 正在预热的那一条支线。指针一次只停在一行上，同一时刻只热一条：上一条还没回来就不再
// 起新的，免得划过一列支线留下一串请求。
let warmingThread: Promise<void> | null = null

/**
 * 指针停在一条支线的入口上（主线消息下面那一行）：把点开后要等的两样先取回缓存——
 * 支线本身连它挂着的那条消息（页头），和支线里最新那一页回复。手上那份还新鲜就不取。
 * 只写缓存，失败不说（`lib/routePrefetch` 的纪律）。
 */
export function prefetchThread(threadId: string): Promise<void> {
  if (warmingThread) return warmingThread
  warmingThread = Promise.all([
    queryClient.prefetchQuery(threadQuery(threadId)),
    prefetchNewestBlocks(threadId).catch(() => {}),
  ])
    .then(() => {})
    .finally(() => {
      warmingThread = null
    })
  return warmingThread
}

export function roomProgressQuery(roomId: string) {
  return queryOptions({
    queryKey: keys.roomProgress(roomId),
    queryFn: (): Promise<TopicProgress> => getProgress(roomId),
  })
}

/** 芝士写下这一轮的清单，房间连接上推来了（`todo` 帧）：放进缓存，不再去问。 */
export function checklistPushed(roomId: string, items: TodoItem[]): void {
  queryClient.setQueryData<TopicProgress>(keys.roomProgress(roomId), { items, updated_at: new Date().toISOString() })
}

/** 连接断过：断开期间推的清单收不到了，看着它的那处重读一次。 */
export function checklistMissed(roomId: string): Promise<void> {
  return refreshQueries({ queryKey: keys.roomProgress(roomId) })
}

type RoomTaskFilter = NonNullable<Parameters<typeof listRoomTasks>[1]>

/**
 * 房间里的任务，按 `filter` 筛（`GET /topics/{id}/tasks` 说明每个选项留什么）。清单
 * 很沉，没变时服务端答 304，手上这份原样留着（见 `readSince`）。
 */
export function roomTasksQuery(roomId: string, filter: RoomTaskFilter) {
  const queryKey = keys.roomTaskList(roomId, filter)
  return queryOptions({
    queryKey,
    queryFn: ({ signal }): Promise<ListPayload<RoomTask>> =>
      fromSnapshot(queryKey, () => listRoomTasks(roomId, filter, queryClient.getQueryData(queryKey), signal)),
  })
}

/** 还开着的任务，只带任务本身、不带对话：面板的角标、频道概览、侧栏挂的是它。 */
export function openRoomTasksQuery(roomId: string) {
  return roomTasksQuery(roomId, { limit: 0, status: 'open' })
}

/** 这个房间当前预览的是哪一份；没有就是 null。 */
export function previewQuery(roomId: string) {
  return queryOptions({
    queryKey: keys.roomPreview(roomId),
    queryFn: async (): Promise<PreviewInfo | null> => (await getPreview(roomId)) ?? null,
  })
}

/**
 * 问一次当前预览指着哪一份。`maxAgeMs` 之内问过的就用那一份（两处轮询同时开着时只
 * 问一次）；正在问的那一次还没回来就等它（路由守卫可能已经替这个房间先问了）。
 */
export function readPreview(roomId: string, maxAgeMs = 0): Promise<PreviewInfo | null> {
  return queryClient.fetchQuery({ ...previewQuery(roomId), staleTime: maxAgeMs })
}

/** 顺手先问一次（打开任务页的导航）；手上那份还新鲜就不问，失败没人看得见。 */
export function prefetchPreview(roomId: string): Promise<void> {
  return queryClient.prefetchQuery(previewQuery(roomId))
}

/** 手上这一份：没问过是 undefined，问过而没有预览是 null。 */
export function heldPreview(roomId: string): PreviewInfo | null | undefined {
  return queryClient.getQueryData<PreviewInfo | null>(keys.roomPreview(roomId))
}

/**
 * 任务房间里那个任务。任务清单里已经有这一件（侧栏、任务清单、时间线带回来的）时，
 * 打开任务页先画那一行，不等这一次读。
 *
 * 手上没有这一件（整页刷新、直接打开链接）就不等快照，直接问：任务页要先有这一件才挂
 * 得上对话栏，而带快照的那次订阅是对话栏挂上之后才发的，等它只能干等到超时。
 */
export function roomTaskQuery(taskId: string) {
  return queryOptions({
    queryKey: keys.roomTask(taskId),
    queryFn: (): Promise<RoomTask> =>
      listedTask(taskId) ? fromSnapshot(keys.roomTask(taskId), () => getTask(taskId)) : getTask(taskId),
    placeholderData: () => listedTask(taskId),
  })
}

/** 缓存里已经有的这一件：任何一份任务清单里的，或对话最新那一段里某一块带着的。 */
export function listedTask(taskId: string): RoomTask | undefined {
  for (const [, data] of queryClient.getQueriesData<unknown>({ predicate: holdsTasks })) {
    const found = tasksIn(data).find((task) => task.id === taskId)
    if (found) return found
  }
  return undefined
}

function holdsTasks(query: { queryKey: readonly unknown[] }): boolean {
  return query.queryKey.includes('tasks') || query.queryKey.includes('blocks')
}

function holdsBlocks(query: { queryKey: readonly unknown[] }): boolean {
  return query.queryKey.includes('blocks')
}

function holdsThreadRows(query: { queryKey: readonly unknown[] }): boolean {
  return query.queryKey.length === 3 && query.queryKey[0] === 'room' && query.queryKey[2] === 'threads'
}

function blocksIn(data: unknown): Block[] {
  const blocks = (data as { blocks?: unknown } | null)?.blocks
  return Array.isArray(blocks) ? (blocks as Block[]) : []
}

function tasksIn(data: unknown): RoomTask[] {
  if (!data || typeof data !== 'object') return []
  // 一页一页读的那种（已结束的任务）：每一页各有一份 `data`。
  if ('pages' in data && Array.isArray(data.pages)) return data.pages.flatMap(tasksIn)
  if ('data' in data && Array.isArray(data.data)) return data.data as RoomTask[]
  // 对话的窗口：每一块带着它那几件（`GET /topics/{id}/blocks` 的 `tasks`）。
  if ('blocks' in data && Array.isArray(data.blocks))
    return (data.blocks as Block[]).flatMap((block) => block.tasks ?? [])
  return []
}
