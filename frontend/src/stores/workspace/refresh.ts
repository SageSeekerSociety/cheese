import type { ComputedRef, Ref } from 'vue'
import type { Topic } from '@/cx_types'
import type { ProjectAccess, ProjectDenied } from './access'

import { unarchiveProject } from '@/api'
import { t } from '@/i18n'
import { type ProjectRefusal, projectRefusal } from '@/lib/addresses'
import { queryClient, refreshQueries } from '@/query/client'
import { keys } from '@/query/keys'
import { patchTopics, refreshTopicRow as refreshRow } from '@/query/project'
import { refreshProjects } from '@/query/projects'

/** 读得到、可以再读一次的查询（`useQuery` 的返回值只用得上这一点）。 */
export interface Refetchable {
  refetch: () => unknown
}

export interface RefreshDeps {
  projectId: Ref<string | null>
  pid: ComputedRef<string>
  me: ComputedRef<string>
  refused: Ref<ProjectRefusal | null>
  denied: Ref<ProjectDenied>
  error: Ref<string | null>
  accessDenied: ComputedRef<ProjectAccess>
  projectsWanted: Ref<boolean>
  topicsRead: Refetchable
  membersRead: Refetchable
  persistLayout: () => void
}

/**
 * 再读一次的那些动作，和让一个项目成为「当前打开的项目」的 `openProject`。
 *
 * 它们改的只是 query 缓存里那几份（写操作也一样，见 `channels.ts`），store 自己不留
 * 数据；`openProject` 是唯一一个会动「当前项目」这个状态的。
 */
export function useRefreshers(deps: RefreshDeps) {
  const {
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
  } = deps

  /** 项目清单变了（新建、归档、转交、退出），或者这一页要用它：再读一次。 */
  function refreshProjectList(): Promise<void> {
    projectsWanted.value = true
    return refreshProjects()
  }

  function refreshTopics(): Promise<void> {
    return refreshQueries({ queryKey: keys.projectTopics(pid.value) })
  }

  function refreshMembers(): Promise<void> {
    return refreshQueries({ queryKey: keys.projectMembers(pid.value) })
  }

  /** 未读、私聊未读和通知档位：数字跟着档位变，三份一起问。 */
  function refreshUnread(): Promise<void> {
    const project = pid.value
    return Promise.all([
      refreshQueries({ queryKey: keys.projectUnread(project, me.value) }),
      refreshQueries({ queryKey: keys.projectPrivateUnread(project, me.value) }),
      refreshQueries({ queryKey: keys.projectNotifyLevels(project) }),
    ]).then(() => undefined)
  }

  /** 这一行变了：只取这一行补进清单（见 `query/project`）。 */
  function refreshTopicRow(topicId: string): Promise<void> {
    return refreshRow(pid.value, topicId)
  }

  /** 本地改了这几行：正在路上的那次单行读是改之前发出去的，也不能再补进来。 */
  async function patchRows(topicId: string, change: (topic: Topic) => Topic) {
    await queryClient.cancelQueries({ queryKey: keys.roomRow(topicId) })
    await patchTopics(pid.value, (rows) => rows.map((topic) => (topic.id === topicId ? change(topic) : topic)))
  }

  /** 进一个项目。上一个项目的「打不开」「报错」不带过来；数据各自按项目读。 */
  function openProject(id: string) {
    if (projectId.value === id && !accessDenied.value) return
    // 回到上次打不开的同一个项目（从登录页回来、刚被请进来）：上次读到的是「进不来」，
    // 这一次按现在的身份重读。
    const retry = projectId.value === id
    projectId.value = id
    refused.value = projectRefusal(id) ?? null
    denied.value = null
    error.value = null
    persistLayout()
    if (retry) {
      void topicsRead.refetch()
      void membersRead.refetch()
      void refreshUnread()
    }
  }

  /** 侧栏那块「加载话题失败」的「重试」：再读一次当前项目的话题清单。 */
  async function reloadTopics() {
    await topicsRead.refetch()
  }

  /** 取消归档正开着的这个项目，然后当作第一次打开它，重新取一遍。 */
  async function unarchiveOpenProject(): Promise<boolean> {
    const id = projectId.value
    if (!id) return false
    try {
      await unarchiveProject(id)
    } catch (e) {
      error.value = e instanceof Error ? e.message : t('shell.workspaceErrors.unarchive')
      return false
    }
    denied.value = null
    await Promise.all([refreshProjectList(), queryClient.invalidateQueries({ queryKey: keys.project(id) })])
    return true
  }

  return {
    refreshProjectList,
    refreshTopics,
    refreshMembers,
    refreshUnread,
    refreshTopicRow,
    patchRows,
    openProject,
    reloadTopics,
    unarchiveOpenProject,
  }
}
