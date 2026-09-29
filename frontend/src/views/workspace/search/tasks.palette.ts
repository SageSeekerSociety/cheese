// 内容里的「任务」：房间里派出去的活。点开进那个房间，打开这张卡。
import type { ContentKind } from './projectSearch'

import { contentSource } from './projectSearch'

import { copyLink, linkOf } from '@/commands/copy'
import { t } from '@/i18n'

export const tasks: ContentKind = {
  id: 'tasks',
  label: 'navigation.palette.tasks',
  only: ['tasks'],
  itemsOf: ({ tasks: hits }, projectId, router) =>
    hits.map((hit) => {
      const to = {
        name: 'workspace-topic',
        params: { projectId, topicId: hit.room_id },
        query: { tab: 'overview', card: hit.id },
      }
      return {
        id: `task:${hit.id}`,
        title: hit.title,
        subtitle: hit.room_title,
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
