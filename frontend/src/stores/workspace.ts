import { computed, ref, toRef } from 'vue'
import { defineStore } from 'pinia'

import { useAccess } from './workspace/access'
import { useChannelActions } from './workspace/channels'
import { useLayout } from './workspace/layout'
import { useProjectQueries } from './workspace/queries'
import { useRefreshers } from './workspace/refresh'

import { identityChanges } from '@/lib/identity'
import { myHandle } from '@/me'
import { reading } from '@/query/project'

export { lastOpenedProjectId, lastPlaceOf } from './workspace/layout'
export type { RememberedPlace } from './workspace/types'

// 当前打开的是哪个项目，和围着它的界面状态（栏宽、上次待的房间、为什么打不开）。
//
// 项目的数据（频道清单、成员、未读、通知档位）不存在这里：它们在 query 缓存里
// （`query/project.ts`），这里只是按当前项目把那几份接出来，写操作改的也是那几份。
//
// 它必须是 store 而不是 provide/inject：项目侧栏走全站的 `sidebar` 具名视图
// (App.vue)，和内容区是两个平级的组件实例，中间没有父子关系可以注入。这也正是
// 侧栏能常驻、内容区随便换页的原因。
//
// 这是**一条** store：名字、state、getters、动作一个都没变，调用方零改动。变的是这个
// 文件的大小：它按职责搬进了 `stores/workspace/` 下的几个模块——持久化的界面偏好
// （`layout.ts`）、「打不开」那四种说法（`access.ts`）、要读的那几份（`queries.ts`）、
// 再读一次和切项目（`refresh.ts`）、写频道和任务（`channels.ts`）。留在本文件的是
// 真正共享的那几位（当前项目、身份、能不能读），和把它们拼成 store 的那一步：每个
// 模块要什么就显式传什么，一个模块读了没人给它的字段是类型错误，不是运行时的
// `undefined`。
export const useWorkspaceStore = defineStore('cxWorkspace', () => {
  const projectId = ref<string | null>(null)
  const pid = computed(() => projectId.value ?? '')
  // 跟着登录的人变：换了人，未读读的是新的人那一份。
  const me = computed(() => {
    void identityChanges.value
    return myHandle()
  })
  const hasProject = computed(() => !!projectId.value)

  const { refused, denied, error, reportError, noteAccess, accessDenied } = useAccess({ me })

  // 这个项目的东西能不能去读：地址换不出来的不去读。自己的项目清单不在此列，照常读。
  const reachable = computed(() => hasProject.value && !refused.value)

  // 进了项目、或者哪一页要了（`refreshProjects`）才读：没打开任何项目时 store 不替谁去问。
  const projectsWanted = ref(false)

  const {
    chatPct,
    panelPref,
    lastPlaceByProject,
    persistLayout,
    setChatPct,
    resetChatPct,
    setPanelPref,
    rememberPlace,
    placeFor,
    forgetPlace,
  } = useLayout(projectId)

  const {
    topicsRead,
    membersRead,
    projects,
    projectsSettled,
    topics,
    loadingTopics,
    members,
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
  } = useProjectQueries({
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
  })

  // What the URL says is open. Set by the shell from the route, read here so a
  // background unread refresh never lights a badge on the thing you're reading.
  const activeTopicId = toRef(reading, 'topicId')
  const activeDmPeer = toRef(reading, 'dmPeer')

  const {
    refreshProjectList,
    refreshTopics,
    refreshMembers,
    refreshUnread,
    refreshTopicRow,
    patchRows,
    openProject,
    reloadTopics,
    unarchiveOpenProject,
  } = useRefreshers({
    projectId,
    pid,
    me,
    refused,
    denied,
    error,
    accessDenied,
    projectsWanted,
    topicsRead,
    membersRead,
    persistLayout,
  })

  const actions = useChannelActions({
    pid,
    projectId,
    me,
    topics,
    unreadMap,
    privateUnreadMap,
    notifyLevels,
    lastPlaceByProject,
    rememberPlace,
    forgetPlace,
    patchRows,
    refreshTopics,
    refreshUnread,
    reportError,
  })

  return {
    projectId,
    projects,
    projectsSettled,
    accessDenied,
    projectRefused: computed(() => refused.value !== null),
    noteAccess,
    openedProject,
    topics,
    members,
    agentName,
    agentHandle,
    isExternal,
    loadingTopics,
    unreadMap,
    notifyLevels,
    levelOf,
    mutedUntil,
    isMuted,
    setNotifyLevel: actions.setNotifyLevel,
    setJoined: actions.setJoined,
    describe: actions.describe,
    setMembersOnly: actions.setMembersOnly,
    markAllRead: actions.markAllRead,
    privateUnreadMap,
    activeTopicId,
    activeDmPeer,
    chatPct,
    panelPref,
    error,
    topicsError,
    rootTopic,
    projectName,
    setChatPct,
    resetChatPct,
    setPanelPref,
    reportError,
    refreshProjects: refreshProjectList,
    refreshMembers,
    refreshTopics,
    reloadTopics,
    refreshUnread,
    refreshTopicRow,
    openProject,
    unarchiveOpenProject,
    markRead: actions.markRead,
    markDmRead: actions.markDmRead,
    renameTopic: actions.renameTopic,
    renameTask: actions.renameTask,
    archive: actions.archive,
    unarchive: actions.unarchive,
    removeChannel: actions.removeChannel,
    rememberPlace,
    placeFor,
    create: actions.create,
    upgradeMessage: actions.upgradeMessage,
  }
})
