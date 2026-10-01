// 内容里的「项目文档」：周报。点开去项目文档页的周报那一格。
import type { ContentKind } from './projectSearch'

import { contentSource, whereAndWhen } from './projectSearch'

import { t } from '@/i18n'
import { relTime } from '@/lib/relTime'

export const projectDocs: ContentKind = {
  id: 'project-docs',
  label: 'navigation.palette.projectDocs',
  only: ['weekly'],
  itemsOf: ({ records }, projectId) =>
    records
      .filter((hit) => hit.kind === 'weekly')
      .map((hit) => ({
        id: `${hit.kind}:${hit.id}`,
        title: hit.snippet,
        subtitle: whereAndWhen(t('navigation.palette.weekly'), relTime(hit.created_at)),
        icon: 'mdi-calendar-text-outline',
        to: { name: 'project-docs', params: { projectId, kind: 'weeklies' } },
      })),
}

export default contentSource(projectDocs, 130)
