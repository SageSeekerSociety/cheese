// 命令面板里的「任务」：房间里派出去的活。点开进那个房间，打开这张卡。
import type { PaletteSource } from '@/commands/palette/sources'

import { hitsFor } from './projectSearch'

const source: PaletteSource = {
  id: 'tasks',
  label: 'navigation.palette.tasks',
  order: 110,
  prefix: '?',
  async search(query, { projectId }) {
    if (!projectId) return []
    const { tasks } = await hitsFor(projectId, query)
    return tasks.map((hit) => ({
      id: `task:${hit.id}`,
      title: hit.title,
      subtitle: hit.room_title,
      icon: 'mdi-checkbox-marked-circle-outline',
      to: {
        name: 'workspace-topic',
        params: { projectId, topicId: hit.room_id },
        query: { tab: 'overview', card: hit.id },
      },
    }))
  },
}

export default source
