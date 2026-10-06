// 内容里的「任务」：房间里派出去的活。点开进那个房间，打开这张卡。
import type { ContentKind } from './projectSearch'

import { contentSource, searchRoomTitle } from './projectSearch'

import { copyLink, linkOf } from '@/commands/copy'
import { t } from '@/i18n'
import { taskTitle } from '@/lib/topicState'

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
        actions: () => [
          {
            id: 'task.copyLink',
            title: t('work.room.menu.copyLink'),
            icon: 'mdi-link-variant',
            run: () => void copyLink(linkOf(router, to)),
          },
        ],
      }
    }),
}

export default contentSource(tasks, 110)
