// 内容里的「任务」：房间里派出去的活。点开进那个房间，打开这张卡。
import type { ContentKind } from './projectSearch'

import { contentSource, searchRoomTitle } from './projectSearch'

import { copyLink, linkOf } from '@/commands/copy'
import { taskActions } from '@/commands/taskActions'
import { mayIn } from '@/lib/projectPermissions'
import { taskTitle } from '@/lib/topicState'
import { myHandle } from '@/me'
import { useWorkspaceStore } from '@/stores/workspace'

export const tasks: ContentKind = {
  id: 'tasks',
  label: 'navigation.palette.tasks',
  only: ['tasks'],
  itemsOf: ({ tasks: hits }, projectId, router) =>
    hits.map((hit) => {
      const to = {
        name: 'workspace-task',
        params: { projectId, topicId: hit.room_id, taskId: hit.id },
      }
      return {
        id: `task:${hit.id}`,
        title: taskTitle(hit),
        subtitle: searchRoomTitle(hit),
        icon: 'mdi-checkbox-marked-circle-outline',
        to,
        // 和侧栏任务行、任务页是同一份（`commands/taskActions.ts`）。搜索结果不带负责人和
        // 协作者，认不出我参不参加，所以这里只有人人能做的那几件；改名、关闭在任务页的
        // 「操作」里。
        actions: () => {
          const store = useWorkspaceStore()
          return taskActions(
            { ...hit, project_id: projectId },
            { rename: () => {}, close: () => {}, reopen: () => void store.reopenTask(hit.id) },
            {
              me: myHandle(),
              worksTasks: mayIn(
                store.projects.find((project) => project.id === projectId),
                'tasks'
              ),
              copyLink: () => void copyLink(linkOf(router, to)),
            }
          )
        },
      }
    }),
}

export default contentSource(tasks, 110)
