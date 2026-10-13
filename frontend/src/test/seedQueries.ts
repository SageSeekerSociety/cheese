// 测试里把服务器数据直接放进缓存（query/client）：store 和页面读到的就是这几份，
// 当作刚读过、还新鲜，挂载时不再发请求。每个用例结束时缓存会清空（setup-network.ts）。
import type { TopicNotifySetting, TopicUnread } from '@/api'
import type { Project, ProjectMemberRow, Topic } from '@/cx_types'

import { myHandle } from '@/me'
import { queryClient } from '@/query/client'
import { keys } from '@/query/keys'

/** 我能看到的项目。 */
export function seedProjects(projects: Project[]): void {
  queryClient.setQueryData(keys.projects(), projects)
}

/** 一个项目的频道清单、成员、未读、通知档位、项目本身：给了哪几样就放哪几样。 */
export function seedProject(
  projectId: string,
  data: {
    topics?: Topic[]
    members?: ProjectMemberRow[]
    unread?: Record<string, TopicUnread>
    privateUnread?: Record<string, number>
    notifyLevels?: Record<string, TopicNotifySetting>
    project?: Project
  },
  me: string = myHandle()
): void {
  if (data.topics)
    queryClient.setQueryData(keys.projectTopics(projectId), { data: data.topics, total: data.topics.length })
  if (data.members) queryClient.setQueryData(keys.projectMembers(projectId), data.members)
  if (data.unread) queryClient.setQueryData(keys.projectUnread(projectId, me), data.unread)
  if (data.privateUnread) queryClient.setQueryData(keys.projectPrivateUnread(projectId, me), data.privateUnread)
  if (data.notifyLevels) queryClient.setQueryData(keys.projectNotifyLevels(projectId), data.notifyLevels)
  if (data.project) queryClient.setQueryData(keys.projectRow(projectId), data.project)
}
