// 芝士面板 @ 菜单的数据：能引用的东西（打开菜单时读一次，过一会儿再打开重读）、最近去过
// （Ctrl+K 记下的那一份）、按打的字搜题目。面板只拿结果，不取数。
import type { Citable } from '@/types/citable'

import { ref } from 'vue'

import { getCitable } from '@/api'
import { TaskBoardApi } from '@/api/taskBoard'
import { readRecents } from '@/commands/palette/recents'

/** 读回来的候选用多久；过了再打开菜单就重读（新建的任务、文档要能挑到）。 */
const FRESH_MS = 60_000

export function useAgentCiting(projectId: () => string | null) {
  const items = ref<Citable[] | null>(null)
  let readAt = 0
  let readFor: string | null = null

  async function load() {
    const project = projectId()
    if (items.value && project === readFor && Date.now() - readAt < FRESH_MS) return
    if (project !== readFor) items.value = null
    readFor = project
    try {
      const { items: found } = await getCitable(project)
      if (readFor === project) {
        items.value = found
        readAt = Date.now()
      }
    } catch {
      if (readFor === project) items.value = []
    }
  }

  /** Ctrl+K 记下的「最近去过」里的频道、任务、支线（`topic:<id>`），近的在前。 */
  function recents(): string[] {
    return readRecents()
      .slice()
      .sort((a, b) => b.at - a.at)
      .flatMap((entry) => (entry.id.startsWith('topic:') ? [entry.id.slice('topic:'.length)] : []))
  }

  async function searchProblems(query: string): Promise<Citable[]> {
    const { tasks } = await TaskBoardApi.search(query)
    return tasks.map((task) => ({
      id: `problem-${task.id}`,
      kind: 'problem' as const,
      title: task.name,
      where: task.spaceName,
    }))
  }

  return { items, load, recents, searchProblems }
}
