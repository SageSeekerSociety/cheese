// 平台发货的 AI 队友预设清单（`GET /agent-types`）：给「默认队友」那一格取候选。
//
// 单独成一格组合式（而不是让组件直接问 `@/api`），是因为组件按规矩不碰 API 层
// （`scripts/import-boundary-ratchet-core.mjs`）。读不出来不抛：预设清单不是这一格
// 能不能用的前提 —— 读不到就只留「沿用上层设置」，让组件把「读不到」讲清楚。
import type { AgentType } from '@/cx_types'

import { onMounted, ref } from 'vue'

import { listAgentTypes } from '@/api'

export function useAgentTypes() {
  const types = ref<AgentType[]>([])
  /** 只区分「读到了」和「没读到」：这一格不需要更细的成败。 */
  const failed = ref(false)

  onMounted(async () => {
    try {
      types.value = (await listAgentTypes()).data
    } catch {
      failed.value = true
    }
  })

  return { types, failed }
}
