// 「题目」：我加入的空间里的题目，按名字和简介搜。点开进题目页，下面一行写着在哪个空间。
// 和项目无关，在哪一页按 Ctrl+K 都能搜到。
import type { PaletteSource } from '@/commands/palette/sources'

import { TaskBoardApi } from '@/api/taskBoard'

const source: PaletteSource = {
  id: 'space-tasks',
  label: 'navigation.palette.spaceTasks',
  order: 105,
  async search(query) {
    if (!query.trim()) return []
    try {
      const { tasks } = await TaskBoardApi.search(query)
      return tasks.map((task) => ({
        id: `space-task:${task.id}`,
        title: task.name,
        subtitle: task.spaceName,
        icon: 'mdi-trophy-outline',
        to: { name: 'TasksDetail', params: { spaceId: String(task.spaceId), taskId: String(task.id) } },
      }))
    } catch {
      return []
    }
  },
}

export default source
