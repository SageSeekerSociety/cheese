import type { ComputedRef, Ref } from 'vue'
import type { TopicNotifyLevel, TopicNotifySetting, TopicUnread } from '@/api'
import type { Project, Topic } from '@/cx_types'
import type { ProjectAccess, ProjectDenied } from './access'
import type { RememberedPlace } from './types'

import { computed, watch } from 'vue'
import { useQuery } from '@tanstack/vue-query'

import { t } from '@/i18n'
import { firstPaintDone } from '@/lib/afterFirstPaint'
import { memberName } from '@/lib/agentNames'
import { externalHandles } from '@/lib/externalMembers'
import { queryClient } from '@/query/client'
import {
  membersQuery,
  notifyLevelsQuery,
  privateUnreadQuery,
  projectQuery,
  topicsQuery,
  unreadQuery,
} from '@/query/project'
import { projectsQuery } from '@/query/projects'

export interface QueriesDeps {
  projectId: Ref<string | null>
  pid: ComputedRef<string>
  hasProject: ComputedRef<boolean>
  /** 这个项目的东西能不能去读：地址换不出来的不去读。自己的项目清单不在此列。 */
  reachable: ComputedRef<boolean>
  accessDenied: ComputedRef<ProjectAccess>
  me: ComputedRef<string>
  /** 进了项目、或者哪一页要了（`refreshProjects`）才读项目清单。 */
  projectsWanted: Ref<boolean>
  denied: Ref<ProjectDenied>
  noteAccess: (e: unknown) => boolean
  lastPlaceByProject: Ref<Record<string, RememberedPlace>>
  forgetPlace: (projectKey: string) => void
}

/**
 * 项目这一层要从服务端读的东西：项目清单、频道清单、成员、未读、通知档位，以及围着
 * 它们从地址和状态里算出来的那些。
 *
 * 项目的数据本身不存在 store 里——它们在 query 缓存里（`query/project.ts`），这里只是
 * 按当前项目把那几份接出来。
 *
 * 读到的话题清单还带着两个「进了这个项目才算数」的信号：读不到时给侧栏就地报错的那
 * 一句（`topicsError`），和清单自己说出来的两件事（项目归档了没有、记着的地方还在
 * 不在）。它们都读这一份清单，所以跟它放在一起。
 */
export function useProjectQueries(deps: QueriesDeps) {
  const {
    projectId,
    pid,
    hasProject,
    reachable,
    accessDenied,
    me,
    projectsWanted,
    denied,
    noteAccess,
    lastPlaceByProject,
    forgetPlace,
  } = deps

  const projectsRead = useQuery(
    computed(() => ({ ...projectsQuery(), enabled: hasProject.value || projectsWanted.value })),
    queryClient
  )
  const projects = computed<Project[]>(() => projectsRead.data.value ?? [])
  // 「清单问过了」——成功、失败、还是空清单，都算问过。它和 `projects.length > 0`
  // 是两件事：后者只知道「手上有货」，前者的意思是「不会再变了，可以据此做决定了」。
  //
  // 壳跟着项目行走，而 WorkspaceEntry 要拿壳决定第一屏。分不开发，就会一直等一个
  // 不会来的答案；或者更坏，拿一个还没到货的清单当「这个项目没有壳」。
  const projectsSettled = computed(() => projectsRead.isFetched.value)

  const topicsRead = useQuery(
    computed(() => ({ ...topicsQuery(pid.value), enabled: reachable.value })),
    queryClient
  )
  const topics = computed<Topic[]>(() => topicsRead.data.value?.data ?? [])
  const loadingTopics = computed(() => reachable.value && topicsRead.isPending.value && !topicsRead.isError.value)

  const membersRead = useQuery(
    computed(() => ({ ...membersQuery(pid.value), enabled: reachable.value })),
    queryClient
  )
  const members = computed(() => membersRead.data.value ?? [])
  // 名册上的外部成员（团队以外、被邀请进这个项目的人）。聊天署名、@ 候选、房间名册
  // 都拿它来挂「外部」那个标，所以放在 store 里算一次，谁问都是同一份。
  const externals = computed(() => externalHandles(members.value))
  function isExternal(handle: string | null | undefined): boolean {
    return !!handle && externals.value.has(handle)
  }
  // 界面上称呼项目 AI 队友用的名字。项目可以给它改名，所以任何一处都不能写死「芝士」；
  // 名册还没到时才退回「芝士」。房间里有自己的 AI 席位时，对话里读的是房间名册
  // （`useRoomRoster`），这里给的是项目默认那一位，供拿不到房间名册的地方用。
  const agentName = computed(
    () => memberName(members.value.find((m) => m.agent && m.project_default)) || t('shell.agentDefaultName')
  )
  // 同一位的 handle：句子里提到它时画成可点的 @chip（UserRef），点了去它的成员页。
  const agentHandle = computed(() => members.value.find((m) => m.agent && m.project_default)?.user_handle ?? null)

  // 每个频道和任务在等我的东西（`TopicUnread`：行上的数字、名字加不加粗、上次读后来了
  // 几条），和私聊未读——私聊按对方的 handle 编址（'cheese' = 和芝士那一间），因为私聊
  // 的行来自成员名单，没有话题 id。频道的数字已经按我设的通知档位算过了（后端）。
  // 这三样只画角标（行上的数字、名字加不加粗、未读点、静音标），不挡内容，所以第一遍
  // 排到首帧之后（`lib/afterFirstPaint.ts`）：开页那一瞬间先把页面自己要的数据发出去，
  // 角标随后补上。`query/` 里的重读逻辑照旧，不动。
  const withMe = computed(() => reachable.value && !!me.value)
  const unreadRead = useQuery(
    computed(() => ({ ...unreadQuery(pid.value, me.value), enabled: withMe.value && firstPaintDone.value })),
    queryClient
  )
  const unreadMap = computed<Record<string, TopicUnread>>(() => unreadRead.data.value ?? {})
  const privateUnreadRead = useQuery(
    computed(() => ({ ...privateUnreadQuery(pid.value, me.value), enabled: withMe.value && firstPaintDone.value })),
    queryClient
  )
  const privateUnreadMap = computed<Record<string, number>>(() => privateUnreadRead.data.value ?? {})
  // 我不在默认档位的频道（{topic_id: {level, muted_until}}）。过了期的静音后端不列；
  // 页面开着的时候静音到点，下一次轮询会把它拿掉。
  const levelsRead = useQuery(
    computed(() => ({ ...notifyLevelsQuery(pid.value), enabled: reachable.value && firstPaintDone.value })),
    queryClient
  )
  const notifyLevels = computed<Record<string, TopicNotifySetting>>(() => levelsRead.data.value ?? {})
  function levelOf(topicId: string): TopicNotifyLevel {
    return notifyLevels.value[topicId]?.level ?? 'mentions'
  }
  function mutedUntil(topicId: string): string | null {
    return notifyLevels.value[topicId]?.muted_until ?? null
  }
  function isMuted(topicId: string): boolean {
    return levelOf(topicId) === 'mute'
  }

  /**
   * 话题清单这一块没读到时服务端给的原因，交给侧栏**就地**显示 + 重试
   * （docs/design-system.md §3.10）。和 `error`（弹一条就走的全局 toast）分开：读不到
   * 话题清单要留在它读的那块地方——那条红条几秒就没，之后和「暂无话题」长得一模一样，
   * 人再也分不出是「坏了」还是「本来就没有」。手上有清单时，后台那一次没读到不算：
   * 清单维持原样。
   */
  const topicsError = computed<string | null>(() => {
    const e = topicsRead.error.value
    if (!e || topicsRead.data.value || accessDenied.value) return null
    return e instanceof Error ? e.message : t('shell.workspaceErrors.loadTopics')
  })
  // 「进不来」和「进来了但这一次没取到」是两件事：前者要一屏说明，后者是侧栏里那块
  // 就地报错。分不开的话，一次网络抖动会被写成「你没有权限」。读到一半登录过期、或者
  // 席位被拿掉，也是这里接住：屏幕上那份清单已经不是这个人能看的了。
  watch(
    () => topicsRead.error.value,
    (e) => {
      if (e) noteAccess(e)
    }
  )
  watch(
    () => topicsRead.data.value,
    (payload) => {
      const list = payload?.data
      if (!list || !projectId.value) return
      // 项目归档了没有，看它的总览房间：总览只会随项目一起归档（单独归档它会被后端拒），
      // 所以这一位不用多发一个请求就读得出来。
      if (list.some((topic) => topic.kind === 'root' && topic.status === 'archived')) denied.value = 'archived'
      // 记着的那一页所在的频道不在了（删了、被收成了别的房间）：忘掉它，rail 这一格
      // 回落项目首页。不在频道里的那些页（总览、成员、资料库）没有这一层校验，它们
      // 的地址不依赖频道。
      const remembered = lastPlaceByProject.value[projectId.value]
      if (remembered?.room && !list.some((row) => row.id === remembered.room)) {
        forgetPlace(projectId.value)
      }
    }
  )

  // 这个项目本身（`projectQuery`）。只在它归档了时才要：归档了的项目不在 `projects` 那份
  // 清单里，名字和所有者得从这一份读。
  const projectRead = useQuery(
    computed(() => ({ ...projectQuery(pid.value), enabled: hasProject.value && accessDenied.value === 'archived' })),
    queryClient
  )
  const openedProject = computed<Project | null>(() => projectRead.data.value ?? null)

  const rootTopic = computed<Topic | null>(() => topics.value.find((t) => t.kind === 'root') ?? null)
  const projectName = computed<string>(
    () =>
      projects.value.find((p) => p.id === projectId.value)?.name ??
      (openedProject.value?.id === projectId.value ? openedProject.value.name : '')
  )

  return {
    projectsRead,
    projects,
    projectsSettled,
    topicsRead,
    topics,
    loadingTopics,
    membersRead,
    members,
    externals,
    isExternal,
    agentName,
    agentHandle,
    unreadMap,
    privateUnreadMap,
    notifyLevels,
    levelOf,
    mutedUntil,
    isMuted,
    topicsError,
    openedProject,
    rootTopic,
    projectName,
  }
}
