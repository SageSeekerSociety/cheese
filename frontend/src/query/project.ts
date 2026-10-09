// 一个项目的频道清单、成员、未读和通知档位：侧栏、头部、@ 候选、私聊列表都读这几份。
//
// 每一份的读法只写在这里。轮询由看着它的那一处加（`ProjectShell` 每 30 秒问一次未读和
// 清单），写操作和推送通过下面这几个函数改缓存，不各存副本。
import type { TopicNotifySetting, TopicUnread } from '@/api'
import type { ListPayload, Project, ProjectAgent, ProjectMemberRow, Topic } from '@/cx_types'
import type { ProgressItem } from '@/types/projectProgress'

import { computed, reactive, type Ref } from 'vue'
import { queryOptions, useQuery } from '@tanstack/vue-query'

import {
  getPrivateUnread,
  getProject,
  getTopic,
  getTopicNotifyLevels,
  getTopicUnread,
  listProjectAgents,
  listProjectMembers,
  listTopics,
} from '@/api'
import { getDocumentText, getProjectOverview } from '@/api/projectDocuments'
import { listProjectProgress } from '@/api/projectProgress'
import { rememberNumbered, rememberProjects } from '@/lib/addresses'
import { cachedWindow, prefetchNewestBlocks } from '@/query/blocks'
import { patchQuery, queryClient, settled } from '@/query/client'
import { keys } from '@/query/keys'

// 最近有动静的在前，不可调：侧栏用位置本身说这个顺序。
const TOPIC_SORT = { sort: 'last_activity_at', order: 'desc' } as const

/**
 * 这个项目本身：名字、所有者、根房间、我管不管成员。归档了的项目不在项目清单里
 * （清单只列在用的），名字和所有者得从这一份读。
 */
export function projectQuery(projectId: string) {
  return queryOptions({
    queryKey: keys.projectRow(projectId),
    queryFn: async (): Promise<Project> => {
      const project = await getProject(projectId)
      rememberProjects([project])
      return project
    },
  })
}

/** 项目的 AI 队友（含停用的）：私聊的地址和标题、成员页那一段、设置里那一节都读它。 */
export function agentsQuery(projectId: string) {
  return queryOptions({
    queryKey: keys.projectAgents(projectId),
    queryFn: async (): Promise<ProjectAgent[]> => (await listProjectAgents(projectId)).data,
  })
}

export function topicsQuery(projectId: string) {
  const queryKey = keys.projectTopics(projectId)
  return queryOptions({
    queryKey,
    queryFn: async ({ signal }): Promise<ListPayload<Topic>> => {
      const payload = await listTopics(projectId, TOPIC_SORT, queryClient.getQueryData(queryKey), signal)
      rememberNumbered('channels', payload.data)
      return payload
    },
  })
}

export function membersQuery(projectId: string) {
  return queryOptions({
    queryKey: keys.projectMembers(projectId),
    queryFn: async (): Promise<ProjectMemberRow[]> => (await listProjectMembers(projectId)).data,
  })
}

/** 地址栏说正开着的房间和私聊（项目外框按路由写）：后台读回来的未读不给它们亮角标。 */
export const reading = reactive({ topicId: null as string | null, dmPeer: null as string | null })

export function unreadQuery(projectId: string, me: string) {
  const queryKey = keys.projectUnread(projectId, me)
  return queryOptions({
    queryKey,
    queryFn: async (): Promise<Record<string, TopicUnread>> => {
      const map = await getTopicUnread(projectId, me)
      // 正在读的这一间不亮。
      if (reading.topicId) delete map[reading.topicId]
      // 未读变多了的房间，把最新一页在后台取进来：切回去时，离开期间来的那条回复第一帧
      // 就在。只取这一趟真的开过（手上有窗口）的：没开过的下次打开本来就要读一次，预取
      // 省不掉它，只会把它挪到最不该发请求的时刻 —— 刷新页面时每一个有未读的房间都算
      // 「变多了」，两百多个房间的项目会同时打出几十个读。
      const before = queryClient.getQueryData<Record<string, TopicUnread>>(queryKey) ?? {}
      for (const [roomId, unread] of Object.entries(map)) {
        if (unread.messages > (before[roomId]?.messages ?? 0) && cachedWindow(roomId)) {
          void prefetchNewestBlocks(roomId, { fresh: true }).catch(() => {})
        }
      }
      return map
    },
  })
}

export function privateUnreadQuery(projectId: string, me: string) {
  return queryOptions({
    queryKey: keys.projectPrivateUnread(projectId, me),
    queryFn: async (): Promise<Record<string, number>> => {
      const map = await getPrivateUnread(projectId, me)
      if (reading.dmPeer) delete map[reading.dmPeer]
      return map
    },
  })
}

export function notifyLevelsQuery(projectId: string) {
  return queryOptions({
    queryKey: keys.projectNotifyLevels(projectId),
    queryFn: (): Promise<Record<string, TopicNotifySetting>> => getTopicNotifyLevels(projectId),
  })
}

/** 项目总览是哪一份文档、现在写着什么。读不到就按「还没写」：那一块的入口（写一份）仍然在。 */
export function overviewQuery(projectId: string) {
  return queryOptions({
    queryKey: keys.projectOverview(projectId),
    queryFn: async (): Promise<{ id: string | null; text: string }> => {
      try {
        const { id } = await getProjectOverview(projectId)
        return { id, text: await getDocumentText(id) }
      } catch {
        return { id: null, text: '' }
      }
    },
  })
}

/** 项目最近的进展。 */
export function progressQuery(projectId: string) {
  return queryOptions({
    queryKey: keys.projectProgress(projectId),
    queryFn: async (): Promise<ProgressItem[]> => (await listProjectProgress(projectId)).data,
  })
}

/** 一个房间这一行：清单里还没有它（深链接进来、刚变得可见的私有频道）时单独取。 */
export function roomRowQuery(roomId: string) {
  return queryOptions({
    queryKey: keys.roomRow(roomId),
    queryFn: (): Promise<Topic> => getTopic(roomId),
  })
}

/** 清单里的这几行换成 `change` 给的样子。 */
export function patchTopics(projectId: string, change: (rows: Topic[]) => Topic[]): Promise<void> {
  return patchQuery<ListPayload<Topic>>(keys.projectTopics(projectId), (payload) => ({
    ...payload,
    data: change(payload.data),
  }))
}

/**
 * 这一行变了（状态、标题、可见性）。只取这一行，补进清单；清单里没有它就整份重读：
 * 它可能是刚变得对这个人可见、此前根本不在清单里的房间，只补一行补不出来。
 */
export async function refreshTopicRow(projectId: string, roomId: string): Promise<void> {
  // 这个项目的清单不在手上（没打开过）：这一行先作废，等有人看清单时整份读。
  if (!queryClient.getQueryData(keys.projectTopics(projectId))) {
    await queryClient.invalidateQueries({ queryKey: keys.roomRow(roomId) })
    return
  }
  // 正在读这一行的那一次是变之前发出去的：等它回来再读，同时要的跟着同一次。
  const inFlight = queryClient.getQueryCache().find({ queryKey: keys.roomRow(roomId), exact: true })
  if (inFlight) await settled(inFlight)
  let row: Topic
  try {
    row = await queryClient.fetchQuery({ ...roomRowQuery(roomId), staleTime: 0 })
  } catch {
    // 被作废（这期间本地改过这一行）或者没取到：清单维持原样。
    return
  }
  const rows = queryClient.getQueryData<ListPayload<Topic>>(keys.projectTopics(projectId))?.data
  if (rows?.some((topic) => topic.id === roomId)) {
    await patchTopics(projectId, (list) => list.map((topic) => (topic.id === roomId ? row : topic)))
  } else {
    await queryClient.invalidateQueries({ queryKey: keys.projectTopics(projectId) })
  }
}

/**
 * 按 id 找这个项目里的一个房间：清单里有就是清单那一行；没有就单独取这一行（深链接
 * 进来、刚变得可见的私有频道），取到了且确实是这个项目的才算。`resolving` 是「还没
 * 问完」，和「问完了，没有这个房间」分开 —— 分不开，深链接进来的第一帧会闪一下「这个
 * 话题不存在」。
 */
export function usePlace(projectId: Ref<string | null>, roomId: Ref<string>, listed: Ref<Topic[]>) {
  const inList = computed(() => listed.value.find((topic) => topic.id === roomId.value) ?? null)
  const row = useQuery(
    computed(() => ({ ...roomRowQuery(roomId.value), enabled: !!roomId.value && !inList.value })),
    queryClient
  )
  const place = computed<Topic | null>(() => {
    if (inList.value) return inList.value
    const fetched = row.data.value
    return fetched && fetched.id === roomId.value && fetched.project_id === projectId.value ? fetched : null
  })
  const resolving = computed(() => !inList.value && row.isFetching.value)
  return { place, resolving }
}
