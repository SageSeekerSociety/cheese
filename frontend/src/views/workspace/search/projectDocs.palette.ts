// 内容里的「项目文档」：决定和周报。点开去项目文档页对应的那一格。
import type { ContentKind } from './projectSearch'

import { contentSource, whereAndWhen } from './projectSearch'

import { t } from '@/i18n'
import { relTime } from '@/lib/relTime'

export const projectDocs: ContentKind = {
  id: 'project-docs',
  label: 'navigation.palette.projectDocs',
  only: ['decision', 'weekly'],
  itemsOf: ({ records }, projectId) =>
    records
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
      }),
}

export default contentSource(projectDocs, 130)
