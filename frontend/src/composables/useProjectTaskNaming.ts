// 项目设置页「任务命名」这一节的数据：没起名的任务由平台自动起名、方向变了再改
// （默认），还是全由人来起名。两档都不碰人定过的名字，行为见后端
// room_task/naming.py。
//
// 拆自 `components/ProjectTaskNamingSettings.vue`：只有取数和保存，选中即保存 ——
// 两个选项，没有要一起提交的别的字段，所以这里没有草稿。
import type { TaskNaming, TaskNamingMode } from '@/api'

import { ref } from 'vue'

import { getTaskNaming, setTaskNaming } from '@/api'
import { t } from '@/i18n'

export function useProjectTaskNaming(projectId: () => string) {
  const state = ref<TaskNaming | null>(null)
  const error = ref('')
  const busy = ref(false)

  async function load() {
    error.value = ''
    try {
      state.value = await getTaskNaming(projectId())
    } catch (e) {
      error.value = e instanceof Error ? e.message : t('work.projectSettings.taskNaming.loadFailed')
    }
  }

  async function choose(mode: TaskNamingMode | null) {
    if (!mode || !state.value?.can_manage || mode === state.value.mode) return
    busy.value = true
    error.value = ''
    try {
      state.value = await setTaskNaming(projectId(), mode)
    } catch (e) {
      error.value = e instanceof Error ? e.message : t('work.projectSettings.taskNaming.saveFailed')
    } finally {
      busy.value = false
    }
  }

  return { state, error, busy, load, choose }
}
