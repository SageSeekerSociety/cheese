// 内容里的「资料库」：按文件名找。点开去资料库。
import type { ContentKind } from './projectSearch'

import { contentSource } from './projectSearch'

export const library: ContentKind = {
  id: 'library',
  label: 'navigation.palette.library',
  only: ['library'],
  itemsOf: ({ library: hits }, projectId) =>
    hits.map((hit) => {
      const slash = hit.path.lastIndexOf('/')
      return {
        id: `library:${hit.path}`,
        title: hit.path.slice(slash + 1),
        subtitle: slash > 0 ? hit.path.slice(0, slash) : undefined,
        icon: 'mdi-file-outline',
        to: { name: 'project-library', params: { projectId } },
      }
    }),
}

export default contentSource(library, 140)
