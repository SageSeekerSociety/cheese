// 新手引导做到哪了：问服务端（`GET /projects/{id}/start-steps`），房间里来了新消息就
// 再问一次。引导（composables/useProjectTour.ts）按这份结果决定往不往下走。
import type { Ref } from 'vue'

import { ref, watch } from 'vue'

import { getStartSteps } from '@/api'

export type StartStepsState = { asked: boolean; answered: boolean; accepted: boolean; learned: boolean }

export function useStartSteps(
  projectId: () => string | null | undefined,
  refreshKey: () => number
): Ref<StartStepsState | null> {
  const steps = ref<StartStepsState | null>(null)
  let asking = 0
  watch(
    () => [projectId(), refreshKey()] as const,
    async ([project], before) => {
      if (before && project !== before[0]) steps.value = null
      if (!project) return
      const mine = ++asking
      try {
        const next = await getStartSteps(project)
        if (mine === asking) steps.value = next
      } catch {
        // 问不到就不画：这是一条提示，不是这一页能不能用的前提。
      }
    },
    { immediate: true }
  )
  return steps
}
