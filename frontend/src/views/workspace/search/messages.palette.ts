// 内容里的「消息」：对话里说过的话。点开进那个房间，停在这一条上；说在某件活的
// 卡片里的，打开那张卡停在这一条上——卡片里的对话不在房间的对话里。
import type { ContentKind } from './projectSearch'

import { contentSource, searchRoomTitle, whereAndWhen } from './projectSearch'

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
        const to = {
          ...room,
          query: hit.task_id ? { tab: 'overview', card: hit.task_id, block: hit.id } : { block: hit.id },
        }
        return {
          id: `message:${hit.id}`,
          title: hit.snippet,
          subtitle: whereAndWhen(searchRoomTitle(hit), `@${hit.author}`, relTime(hit.created_at)),
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
