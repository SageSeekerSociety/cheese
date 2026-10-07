// 「这个项目还剩多少额度」那一块的事实：请求落在这里，
// `components/settings/CreditsPanel.vue` 只画收到的状态——组件不吃 API 层
// （.claude/rules/architecture.md）。什么时候取由调用方决定：面板按 `projectId`
// 变化触发，并在首次取数期间占住设置页的显示闸。
import type { ProjectCredits } from '@/cx_types'

import { ref } from 'vue'

import { getProjectCredits } from '@/api'

export function useProjectCredits() {
  const credits = ref<ProjectCredits | null>(null)

  /** 最近一次要取的是哪个项目。慢的那个请求后到会盖掉新项目的额度，只认它的答案。 */
  let wanted: string | null = null

  /** 取这个项目的额度；拿不到就留 `null`（「暂无额度信息」与「不限量」是两回事）。 */
  async function load(projectId: string): Promise<void> {
    wanted = projectId
    try {
      const got = await getProjectCredits(projectId)
      if (wanted === projectId) credits.value = got
    } catch {
      if (wanted === projectId) credits.value = null
    }
  }

  return { credits, load }
}
