// 命令面板里的「消息」：对话里说过的话。点开进那个房间。
import type { PaletteSource } from '@/commands/palette/sources'

import { hitsFor, whereAndWhen } from './projectSearch'

import { relTime } from '@/lib/relTime'

const source: PaletteSource = {
  id: 'messages',
  label: 'navigation.palette.messages',
  order: 100,
  prefix: '?',
  async search(query, { projectId }) {
    if (!projectId) return []
    const { records } = await hitsFor(projectId, query)
    return records
      .filter((hit) => hit.kind === 'message')
      .map((hit) => ({
        id: `message:${hit.id}`,
        title: hit.snippet,
        subtitle: whereAndWhen(hit.room_title, `@${hit.author}`, relTime(hit.created_at)),
        icon: 'mdi-message-outline',
        to: { name: 'workspace-topic', params: { projectId, topicId: hit.room_id } },
      }))
  },
}

export default source
