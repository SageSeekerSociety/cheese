// 内容结果的最后一行「查看全部结果」：面板里每类只列前几条，要看全就进搜索结果页。
// 它是一行而不是组标题边上的链接，用键盘选得到。有内容结果才出现。
import type { PaletteSource } from '@/commands/palette/sources'

import { hitsFor } from './projectSearch'

import { t } from '@/i18n'

const source: PaletteSource = {
  id: 'search-all',
  // 不另起组名：它跟在内容那几组后面。
  label: '',
  order: 190,
  prefix: '?',
  async search(query, { projectId }) {
    if (!projectId) return []
    const { records, tasks, library } = await hitsFor(projectId, query)
    if (!records.length && !tasks.length && !library.length) return []
    return [
      {
        id: 'search-all',
        title: t('navigation.palette.searchAll'),
        icon: 'mdi-text-search',
        to: { name: 'project-search', params: { projectId }, query: { q: query } },
      },
    ]
  },
}

export default source
