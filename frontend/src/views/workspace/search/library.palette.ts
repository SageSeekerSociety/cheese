// 命令面板里的「资料库」：按文件名找。点开去资料库。
import type { PaletteSource } from '@/commands/palette/sources'

import { hitsFor } from './projectSearch'

const source: PaletteSource = {
  id: 'library',
  label: 'navigation.palette.library',
  order: 140,
  prefix: '?',
  async search(query, { projectId }) {
    if (!projectId) return []
    const { library } = await hitsFor(projectId, query)
    return library.map((hit) => {
      const slash = hit.path.lastIndexOf('/')
      return {
        id: `library:${hit.path}`,
        title: hit.path.slice(slash + 1),
        subtitle: slash > 0 ? hit.path.slice(0, slash) : undefined,
        icon: 'mdi-file-outline',
        to: { name: 'project-library', params: { projectId } },
      }
    })
  },
}

export default source
