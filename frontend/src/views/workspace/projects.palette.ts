// 命令面板里的「项目」：我能进的所有项目，在哪儿都能跳。
import type { PaletteItem, PaletteSource } from '@/commands/palette/sources'
import type { Project } from '@/cx_types'

import { useWorkspaceStore } from '@/stores/workspace'

function itemOf(project: Project): PaletteItem {
  return {
    id: `project:${project.id}`,
    title: project.name,
    icon: 'mdi-folder-outline',
    to: { name: 'workspace-project', params: { projectId: project.id } },
    scope: project.id,
  }
}

const source: PaletteSource = {
  id: 'projects',
  label: 'navigation.palette.projects',
  order: 20,
  items: () => useWorkspaceStore().projects.map(itemOf),
}

export default source
