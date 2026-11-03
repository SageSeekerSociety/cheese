// 平台发货的技能目录（`GET /skill-catalog`）：给题目「技能」那一格取候选。
//
// 和 `useAgentTypes` 同一个理由单独成一格组合式：组件不碰 API 层。读不出来不抛 ——
// 已经选上的技能照样列着、照样能删，只是没有别的可加，组件把「读不到」讲清楚。
import { onMounted, ref } from 'vue'

import { listPlatformSkills } from '@/api/projectSkills'

export interface SkillChoice {
  name: string
  description: string
}

export function useSkillCatalog() {
  const skills = ref<SkillChoice[]>([])
  const failed = ref(false)

  onMounted(async () => {
    try {
      skills.value = (await listPlatformSkills()).data
    } catch {
      failed.value = true
    }
  })

  return { skills, failed }
}
