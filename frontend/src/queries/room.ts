// 一个房间（频道、任务、私聊）围着对话的那几份：名册、进度、进行中的任务、当前预览、
// 任务房间里那个任务。房间头部、右侧面板、对话栏、@ 候选读的是同一份。
import type { Block, ListPayload, PreviewInfo, RoomTask, TopicMemberRow, TopicProgress } from '@/cx_types'
import type { ChannelPin } from '@/types/channels'
import type { TopicComputeProfile } from '@/types/compute'
import type { ThreadRow } from '@/types/threads'

import { queryOptions } from '@tanstack/vue-query'

import { getPreview, getProgress, getTask, getTopicComputeProfile, listRoomTasks, listTopicMembers } from '@/api'
import { listPins } from '@/api/pins'
import { listThreads } from '@/api/threads'
import { queryClient } from '@/lib/queryClient'
import { keys } from '@/queries/keys'

/** 房间名册。名册的写法（`api/topicMembers`）写完就把它标过期。 */
export function roomMembersQuery(roomId: string) {
  return queryOptions({
    queryKey: keys.roomMembers(roomId),
    queryFn: (): Promise<ListPayload<TopicMemberRow>> => listTopicMembers(roomId),
  })
}

/** 这个房间在哪台电脑上做、每个会话在哪台机器上。 */
export function computeProfileQuery(roomId: string) {
  return queryOptions({
    queryKey: keys.roomComputeProfile(roomId),
    queryFn: (): Promise<TopicComputeProfile> => getTopicComputeProfile(roomId),
  })
}

/** 频道的置顶：概览里那一块和对话栏里的小图钉读同一份。 */
export function pinsQuery(roomId: string) {
  return queryOptions({
    queryKey: keys.roomPins(roomId),
    queryFn: (): Promise<ChannelPin[]> => listPins(roomId),
  })
}

/** 频道的支线，最近有回复的在前：概览里那一格和主线消息下面那一行读同一份。 */
export function threadsQuery(roomId: string) {
  return queryOptions({
    queryKey: keys.roomThreads(roomId),
    queryFn: async (): Promise<ThreadRow[]> => {
      const rows = await listThreads(roomId, 100)
      return Array.isArray(rows) ? rows : []
    },
  })
}

export function roomProgressQuery(roomId: string) {
  return queryOptions({
    queryKey: keys.roomProgress(roomId),
    queryFn: (): Promise<TopicProgress> => getProgress(roomId),
  })
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
    queryFn: (): Promise<ListPayload<RoomTask>> => listRoomTasks(roomId, filter, queryClient.getQueryData(queryKey)),
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
 */
export function roomTaskQuery(taskId: string) {
  return queryOptions({
    queryKey: keys.roomTask(taskId),
    queryFn: (): Promise<RoomTask> => getTask(taskId),
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

/** 这个房间的任务变了（新建、改名、关闭、换人）：房间和项目的任务清单都作废。 */
export function refreshTasks(roomId: string, projectId: string | null): Promise<void> {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: keys.roomTasks(roomId) }),
    projectId ? queryClient.invalidateQueries({ queryKey: keys.projectTasks(projectId) }) : null,
  ]).then(() => undefined)
}
