// 项目设置页「工作电脑」这一节的数据：新 agent 开工时用哪台，以及已经在跑的
// agent 现在各在哪。项目只给默认，改默认不搬已经在干活的那几个。
//
// 拆自 `components/ProjectComputeSettings.vue`：取数与保存在这一层，组件只画。
// 保存成功后要在 `window` 上发一条 `project-compute-updated` —— 同一页上的房间
// 选择器、正在看这一页的别的标签页靠它跟着换（见 `useTopicComputeChoice`），
// 所以这条广播留在保存里面，别让组件自己记得发。
import type { ComputeChoice, ProjectComputeConfigs } from '@/types/compute'

import { ref } from 'vue'

import { getProjectComputeConfigs, saveProjectComputeConfigs } from '@/api'
import { t } from '@/i18n'

export function useProjectCompute(projectId: () => string) {
  const state = ref<ProjectComputeConfigs | null>(null)
  const error = ref('')
  const busy = ref(false)

  async function load() {
    error.value = ''
    try {
      state.value = await getProjectComputeConfigs(projectId())
    } catch (e) {
      error.value = e instanceof Error ? e.message : t('work.projectMachine.loadFailed')
    }
  }

  /** 保存默认环境。返回这一次成没成，好让表单决定收不收起来。 */
  async function save(choice: ComputeChoice): Promise<boolean> {
    if (!state.value?.can_manage) return false
    busy.value = true
    error.value = ''
    try {
      const result = await saveProjectComputeConfigs(projectId(), { default: choice })
      state.value = { ...state.value, ...result }
      window.dispatchEvent(new Event('project-compute-updated'))
      return true
    } catch (e) {
      error.value = e instanceof Error ? e.message : t('work.projectMachine.saveFailed')
      return false
    } finally {
      busy.value = false
    }
  }

  return { state, error, busy, load, save }
}
