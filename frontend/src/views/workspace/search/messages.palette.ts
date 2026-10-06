// 内容里的「消息」：对话里说过的话。点开进那个房间，停在这一条上；说在某个任务里
// 的，打开那个任务停在这一条上——任务的对话不在房间的对话里。
import type { ContentKind } from './projectSearch'

import { contentSource, searchAuthor, searchRoomTitle, whereAndWhen } from './projectSearch'

import { copyLink, linkOf } from '@/commands/copy'
import { t } from '@/i18n'
import { relTime } from '@/lib/relTime'

export const messages: ContentKind = {
  id: 'messages',
  label: 'navigation.palette.messages',
  only: ['message'],
  itemsOf: ({ records }, projectId, router) =>
    records
      .filter((hit) => hit.kind === 'message')
      .map((hit) => {
        const room = { name: 'workspace-topic', params: { projectId, topicId: hit.room_id } }
        // 任务里说的话在任务页上，房间里说的在房间页上；都停在那一条。
        const to = hit.task_id
          ? {
              name: 'workspace-task',
              params: { projectId, topicId: hit.room_id, taskId: hit.task_id },
              query: { block: hit.id },
            }
          : { ...room, query: { block: hit.id } }
        return {
          id: `message:${hit.id}`,
          title: hit.snippet,
          subtitle: whereAndWhen(searchRoomTitle(hit), searchAuthor(hit), relTime(hit.created_at)),
          icon: 'mdi-message-outline',
          verb: t('navigation.palette.verbLocate'),
          to,
          actions: () => [
            {
              id: 'message.copyLink',
              title: t('work.room.menu.copyLink'),
              icon: 'mdi-link-variant',
              run: () => void copyLink(linkOf(router, to)),
            },
            { id: 'message.openRoom', title: t('navigation.palette.openRoom'), icon: 'mdi-pound', to: room },
          ],
        }
      }),
}

export default contentSource(messages, 100)
