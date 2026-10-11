// 「任务命名」那一块的数据与动作。
//
// 拆自 `components/ProjectTaskNamingSettings.vue`：那件现在只吃 props、只往上发事件
// （`frontend_grade.py` 的 A 级），取数与发请求落在这里，页面接线 —— 和
// `useBranchProtection.ts` 一个路子，也是 `.claude/rules/architecture.md`「组件不取数」
// 那一条要求的。
//
// 两档都不碰人定过的名字（行为见后端 `room_task/naming.py`）：选中即保存，没有别的
// 字段要一起提交，所以这里没有草稿 —— 控件点一下就发一次请求。
//
// `enabled`：设置页一次只画一栏，这一栏没被打开过就不读（原来那一件只有被画出来才会
// 挂上、才会取数）。给 `usePageActive` 那样一个「这一刻在不在屏幕上」的读口。
import type { TaskNaming, TaskNamingMode } from '@/api'

import { onMounted, ref, watch } from 'vue'

import { holdRevealUntil } from '@/composables/useRevealGate'

import { getTaskNaming, setTaskNaming } from '@/api'
import { t } from '@/i18n'

export interface UseProjectTaskNamingSettingsOptions {
  /** 这一栏此刻在不在屏幕上：第一次为真才读。不给就是挂上即读。 */
  enabled?: () => boolean
  revealGate?: { hold: () => () => void }
}

export function useProjectTaskNamingSettings(
  projectId: () => string,
  options: UseProjectTaskNamingSettingsOptions = {}
) {
  const enabled = options.enabled ?? (() => true)
  const state = ref<TaskNaming | null>(null)
  const error = ref('')
  const busy = ref(false)
  // 第一次取数有没有落定：显示闸靠它开，见 useRevealGate。
  const settled = ref(false)
  let generation = 0
  let actionGeneration = 0
  let saveRevision = 0

  async function load() {
    settled.value = false
    const current = ++generation
    const revision = saveRevision
    error.value = ''
    try {
      const result = await getTaskNaming(projectId())
      if (current === generation && revision === saveRevision) state.value = result
    } catch (e) {
      if (current === generation)
        error.value = e instanceof Error ? e.message : t('work.projectSettings.taskNaming.loadFailed')
    } finally {
      if (current === generation) settled.value = true
    }
  }

  async function choose(mode: TaskNamingMode) {
    if (!state.value?.can_manage || busy.value || mode === state.value.mode) return
    const current = ++actionGeneration
    busy.value = true
    error.value = ''
    try {
      const result = await setTaskNaming(projectId(), mode)
      if (current !== actionGeneration) return
      saveRevision++
      state.value = result
    } catch (e) {
      if (current === actionGeneration)
        error.value = e instanceof Error ? e.message : t('work.projectSettings.taskNaming.saveFailed')
    } finally {
      if (current === actionGeneration) busy.value = false
    }
  }

  // 首次取数期间占住设置页的显示闸；这一栏没被打开过就不占（`!enabled()` 一上来就为真）。
  holdRevealUntil(() => settled.value, enabled, options.revealGate)
  onMounted(() => {
    if (enabled()) void load()
  })
  watch(enabled, (on) => {
    if (on) void load()
    else {
      generation++
      actionGeneration++
      settled.value = false
      state.value = null
      error.value = ''
      busy.value = false
    }
  })
  watch(projectId, () => {
    generation++
    actionGeneration++
    settled.value = false
    state.value = null
    error.value = ''
    busy.value = false
    if (enabled()) void load()
  })

  return { state, error, busy, load, choose }
}
