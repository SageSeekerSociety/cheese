// 内容里的「文档」：房间的文档正文和文档边上的评论。点开进那个房间的总览。
import type { ContentKind } from './projectSearch'

import { contentSource, searchAuthor, searchRoomTitle, whereAndWhen } from './projectSearch'

import { t } from '@/i18n'
import { relTime } from '@/lib/relTime'

export const docs: ContentKind = {
  id: 'docs',
  label: 'navigation.palette.docs',
  only: ['doc', 'doc_node', 'comment'],
  itemsOf: ({ records }, projectId) =>
    records
      .filter((hit) => hit.kind === 'doc' || hit.kind === 'doc_node' || hit.kind === 'comment')
      .map((hit) => {
        const comment = hit.kind === 'comment'
        return {
          id: `doc:${hit.id}`,
          title: hit.snippet,
          subtitle: comment
            ? whereAndWhen(searchRoomTitle(hit), searchAuthor(hit), relTime(hit.created_at))
            : searchRoomTitle(hit),
          icon: comment ? 'mdi-comment-text-outline' : 'mdi-file-document-outline',
          badge: comment ? { text: t('navigation.palette.comment') } : undefined,
          to: { name: 'workspace-topic', params: { projectId, topicId: hit.room_id }, query: { tab: 'overview' } },
        }
      }),
}

export default contentSource(docs, 120)
