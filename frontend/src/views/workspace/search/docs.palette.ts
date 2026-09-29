// 命令面板里的「文档」：房间的文档正文和文档边上的评论。点开进那个房间的总览。
import type { PaletteSource } from '@/commands/palette/sources'

import { hitsFor, whereAndWhen } from './projectSearch'

import { t } from '@/i18n'
import { relTime } from '@/lib/relTime'

const source: PaletteSource = {
  id: 'docs',
  label: 'navigation.palette.docs',
  order: 120,
  prefix: '?',
  async search(query, { projectId }) {
    if (!projectId) return []
    const { records } = await hitsFor(projectId, query)
    return records
      .filter((hit) => hit.kind === 'doc' || hit.kind === 'doc_node' || hit.kind === 'comment')
      .map((hit) => {
        const comment = hit.kind === 'comment'
        return {
          id: `doc:${hit.id}`,
          title: hit.snippet,
          subtitle: comment ? whereAndWhen(hit.room_title, `@${hit.author}`, relTime(hit.created_at)) : hit.room_title,
          icon: comment ? 'mdi-comment-text-outline' : 'mdi-file-document-outline',
          badge: comment ? { text: t('navigation.palette.comment') } : undefined,
          to: { name: 'workspace-topic', params: { projectId, topicId: hit.room_id }, query: { tab: 'overview' } },
        }
      })
  },
}

export default source
