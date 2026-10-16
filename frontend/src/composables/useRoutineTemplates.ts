// 一个项目里存下来的模板：读一次、加一条、改一条、删一条。
//
// 两处用它（项目总览页、房间右侧那一格），问的是同一个问题，所以只有这一份。存在后端、
// 挂在项目上（`api/routines.ts` 那四个接口），画的那一半
// （`components/routine/RoutineCompose.vue`）只收结果。
//
// 它不是规则：没有谁在跑、没有执行记录，只是「填表的起点」。所以读不到时按「这一段没
// 有」算，这一页照样画得出来 —— 一条模板列不出来，不该把整页弄成一片红。
import type { RoutineInput } from '../lib/routine'
import type { RoutineTemplate, RoutineTemplateInput } from '../lib/routineTemplates'

import { ref, watch } from 'vue'

import {
  createRoutineTemplate,
  deleteRoutineTemplate,
  listRoutineTemplates,
  updateRoutineTemplate,
} from '../api/routines'

export function useRoutineTemplates(projectId: () => string) {
  const templates = ref<RoutineTemplate[]>([])

  async function reload() {
    // 房间里那一格在「话题还没选定」的那一帧也在：此刻没有项目可问，问了只会拿到一个
    // 404。等话题到了，下面那次 watch 会重来一遍。
    const project = projectId()
    if (!project) return
    try {
      templates.value = (await listRoutineTemplates(project)).data
    } catch {
      templates.value = []
    }
  }

  // 换了项目就换一份名单：A 项目里存的模板不该出现在 B 项目里。
  watch(projectId, () => void reload(), { immediate: true })

  /** 存下来一条（「存成模板」那一颗）。没存上返回 false，调用方自己说一句。 */
  async function add(body: RoutineInput): Promise<boolean> {
    const project = projectId()
    if (!project) return false
    try {
      const saved = await createRoutineTemplate(project, {
        // 名字可以省，省了后端从目标的第一句里取（`name_of`），和建一条规则时一样。
        title: body.title,
        goal: body.instructions,
        note: body.note?.trim() ? body.note : undefined,
        trigger: body.trigger,
        spec: body.spec,
      })
      templates.value = [saved, ...templates.value]
      return true
    } catch {
      return false
    }
  }

  /**
   * 改一条模板本身（模板页那一行上的「修改」）。没改上返回 false。
   *
   * 换掉列表里那一条，不重跑一遍名单：后端回的就是改好之后的那一行。已经照这条模板建
   * 出来的规则不动 —— 那些规则在另一个名单上，谁也不该被这里牵动。
   */
  async function change(id: string, body: RoutineTemplateInput): Promise<boolean> {
    try {
      const saved = await updateRoutineTemplate(id, body)
      templates.value = templates.value.map((tpl) => (tpl.id === id ? saved : tpl))
      return true
    } catch {
      return false
    }
  }

  /** 删一条。没删掉返回 false。 */
  async function remove(id: string): Promise<boolean> {
    try {
      await deleteRoutineTemplate(id)
      templates.value = templates.value.filter((tpl) => tpl.id !== id)
      return true
    } catch {
      return false
    }
  }

  return { templates, reload, add, change, remove }
}
