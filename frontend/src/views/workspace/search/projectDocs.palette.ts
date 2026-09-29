// 命令面板里的「项目文档」：决定和周报。点开去项目文档页对应的那一格。
import type { PaletteSource } from '@/commands/palette/sources'

import { hitsFor, whereAndWhen } from './projectSearch'

import { t } from '@/i18n'
import { relTime } from '@/lib/relTime'

const source: PaletteSource = {
  id: 'project-docs',
  label: 'navigation.palette.projectDocs',
  order: 130,
  prefix: '?',
  async search(query, { projectId }) {
    if (!projectId) return []
    const { records } = await hitsFor(projectId, query)
    return records
      .filter((hit) => hit.kind === 'decision' || hit.kind === 'weekly')
      .map((hit) => {
        const decision = hit.kind === 'decision'
        return {
          id: `${hit.kind}:${hit.id}`,
          title: hit.snippet,
          subtitle: whereAndWhen(
            t(decision ? 'navigation.palette.decision' : 'navigation.palette.weekly'),
            relTime(hit.created_at)
          ),
          icon: decision ? 'mdi-gavel' : 'mdi-calendar-text-outline',
          to: { name: 'project-docs', params: { projectId, kind: decision ? 'decisions' : 'weeklies' } },
        }
      })
  },
}

export default source
