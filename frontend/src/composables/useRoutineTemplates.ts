// 一个项目里「我存的」模板：读一次、加一条、删一条。
//
// 两处用它（项目总览页、房间右侧那一格），问的是同一个问题，所以只有这一份。存在哪里
// 由 `lib/routineTemplateStore.ts` 决定（此刻是这台浏览器），画的那一半
// （`components/routine/RoutineCompose.vue`）只收结果 —— 换成后端存的那天，改这两个
// 文件，两处一起跟上。
import type { RoutineInput } from '../lib/routine'
import type { RoutineTemplate } from '../lib/routineTemplates'

import { ref, watch } from 'vue'

import { addTemplate, readTemplates, removeTemplate } from '../lib/routineTemplateStore'

export function useRoutineTemplates(projectId: () => string) {
  const templates = ref<RoutineTemplate[]>([])

  function reload() {
    templates.value = readTemplates(projectId())
  }

  // 换了项目就换一份名单：A 项目里存的模板不该出现在 B 项目里。
  watch(projectId, reload, { immediate: true })

  /** 存下来一条（「存成模板」那一颗）。存不进去（这台浏览器不留东西、写满了）返回 false。 */
  function add(body: RoutineInput): boolean {
    const saved = addTemplate(projectId(), body)
    if (!saved) return false
    reload()
    return true
  }

  function remove(id: string) {
    templates.value = removeTemplate(projectId(), id)
  }

  return { templates, reload, add, remove }
}
