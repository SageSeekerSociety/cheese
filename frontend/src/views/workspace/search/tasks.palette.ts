// 命令面板里的「任务」：房间里派出去的活。点开进那个房间，打开这张卡。
import type { PaletteSource } from '@/commands/palette/sources'

import { hitsFor } from './projectSearch'

import { copyLink, linkOf } from '@/commands/copy'
import { t } from '@/i18n'

const source: PaletteSource = {
  id: 'tasks',
  label: 'navigation.palette.tasks',
  order: 110,
  prefix: '?',
  async search(query, { projectId, router }) {
    if (!projectId) return []
    const { tasks } = await hitsFor(projectId, query)
    return tasks.map((hit) => {
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
    })
  },
}

export default source
