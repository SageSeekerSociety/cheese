// 「工作电脑」那一块的数据与动作（项目只给默认：新 agent 开工时用哪台）。
//
// 拆自 `components/ProjectComputeSettings.vue`：那件现在只吃 props、只往上发事件
// （`frontend_grade.py` 的 A 级），取数与发请求落在这里，页面接线。和
// `useBranchProtection.ts` 一个路子（`.claude/rules/architecture.md`「组件不取数」）。
//
// `editing`（「更换」按下去展开表单）也留在这一层：保存成功要把表单收起来，而「成功」
// 只有发请求的这一层知道，跟着草稿一起留在组件里的话，组件就得猜自己有没有存成。
//
// 每次落好默认都广播一条 `project-compute-updated`：正开着的房间据此把「现在的分布」
// 那一行重画（组件那一侧拿不到这一层的事件，广播留给这一层做）。
//
// `enabled`：设置页一次只画一栏，这一栏没被打开过就不读（原来那一件只有被画出来才会
// 挂上、才会取数）。
import type { ComputeChoice, ProjectComputeConfigs } from '@/types/compute'

import { onMounted, ref, watch } from 'vue'

import { holdRevealUntil } from '@/composables/useRevealGate'

import { getProjectComputeConfigs, saveProjectComputeConfigs } from '@/api'
import { t } from '@/i18n'

export interface UseProjectComputeSettingsOptions {
  /** 这一栏此刻在不在屏幕上：第一次为真才读。不给就是挂上即读。 */
  enabled?: () => boolean
  revealGate?: { hold: () => () => void }
}

export function useProjectComputeSettings(projectId: () => string, options: UseProjectComputeSettingsOptions = {}) {
  const enabled = options.enabled ?? (() => true)
  const state = ref<ProjectComputeConfigs | null>(null)
  const error = ref('')
  const busy = ref(false)
  const editing = ref(false)
  // 第一次取数有没有落定：显示闸靠它开，见 useRevealGate。
  const settled = ref(false)
  let generation = 0

  async function load() {
    settled.value = false
    const current = ++generation
    error.value = ''
    try {
      const result = await getProjectComputeConfigs(projectId())
      if (current === generation) state.value = result
    } catch (e) {
      if (current === generation) error.value = e instanceof Error ? e.message : t('work.projectMachine.loadFailed')
    } finally {
      if (current === generation) settled.value = true
    }
  }

  async function save(choice: ComputeChoice) {
    if (!state.value?.can_manage) return
    busy.value = true
    error.value = ''
    try {
      const result = await saveProjectComputeConfigs(projectId(), { default: choice })
      state.value = { ...state.value, ...result }
      window.dispatchEvent(new Event('project-compute-updated'))
      editing.value = false
    } catch (e) {
      error.value = e instanceof Error ? e.message : t('work.projectMachine.saveFailed')
    } finally {
      busy.value = false
    }
  }

  /** 「更换 / 收起」：展开或收起那份表单。不能管项目的人没有这颗按钮。 */
  function toggleEditing() {
    editing.value = !editing.value
  }

  // 首次取数期间占住设置页的显示闸；这一栏没被打开过就不占。
  holdRevealUntil(() => settled.value, enabled, options.revealGate)
  onMounted(() => {
    if (enabled()) void load()
  })
  watch(enabled, (on) => {
    if (on) void load()
    else {
      generation++
      busy.value = false
      settled.value = false
      editing.value = false
      state.value = null
      error.value = ''
    }
  })
  watch(projectId, () => {
    if (enabled()) void load()
  })

  return { state, error, busy, editing, load, save, toggleEditing }
}
