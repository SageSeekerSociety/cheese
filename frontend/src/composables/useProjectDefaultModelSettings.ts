// 「项目默认模型」那一块的数据与动作。
//
// 拆自 `components/ProjectDefaultModelSettings.vue`：那件现在只吃 props、只往上发事件
// （`frontend_grade.py` 的 A 级），取数与发请求落在这里，页面接线。和
// `useBranchProtection.ts` 一个路子（`.claude/rules/architecture.md`「组件不取数」）。
//
// 编辑草稿（还没保存的那个下拉值、分身那一档）留在组件里：那份 `state` 一换（首次取到、
// 或保存后服务端回话）草稿就归位，是组件自己一眼看得见的事，不用往上报。
//
// #1365 之后主线（房间聊天）读 `binding.resolve(None, …)`，它拿 catalog 里 default=True
// 的那条；catalog 由 model_choices 算，项目 settings 里显式写过 default_model 就把那条
// 标 True。这一节就是那个写入口——之前只能手改数据库，对应事故就是「agent 配了 deepseek，
// 主线静默换到订阅 sonnet」。
//
// `enabled`：设置页一次只画一栏，这一栏没被打开过就不读（原来那一件只有被画出来才会
// 挂上、才会取数）。
import type { ProjectDefaultModel } from '@/api'

import { onMounted, ref, watch } from 'vue'

import { holdRevealUntil } from '@/composables/useRevealGate'
import { useSaveState } from '@/composables/useSaveState'

import { getProjectDefaultModel, setProjectDefaultModel } from '@/api'
import { t } from '@/i18n'
import { queryClient } from '@/query/client'
import { keys } from '@/query/keys'

export interface UseProjectDefaultModelSettingsOptions {
  /** 这一栏此刻在不在屏幕上：第一次为真才读。不给就是挂上即读。 */
  enabled?: () => boolean
  revealGate?: { hold: () => () => void }
}

export function useProjectDefaultModelSettings(
  projectId: () => string,
  options: UseProjectDefaultModelSettingsOptions = {}
) {
  const enabled = options.enabled ?? (() => true)
  const state = ref<ProjectDefaultModel | null>(null)
  const loadError = ref('')
  // 第一次取数有没有落定：显示闸靠它开，见 useRevealGate。
  const settled = ref(false)
  let generation = 0

  // 保存结果就地回执（§3.11）：下拉旁边那一行，不再只靠按钮转圈。
  const {
    saving,
    saved,
    error: saveError,
    run,
    reset,
  } = useSaveState({
    feedback: 'inline',
    messages: { failed: t('work.projectSettings.defaultModelBlock.saveFailed') },
  })

  async function load() {
    settled.value = false
    const current = ++generation
    loadError.value = ''
    reset()
    try {
      const id = projectId()
      const result = await queryClient.fetchQuery({
        queryKey: keys.projectDefaultModel(id),
        queryFn: () => getProjectDefaultModel(id),
        staleTime: 0,
      })
      if (current === generation) state.value = result
    } catch (e) {
      if (current === generation)
        loadError.value = e instanceof Error ? e.message : t('work.projectSettings.defaultModelBlock.loadFailed')
    } finally {
      if (current === generation) settled.value = true
    }
  }

  async function save(model: string | null, subagent: string | null) {
    if (!state.value) return
    const current = generation
    await run(async () => {
      const result = await setProjectDefaultModel(projectId(), model, subagent)
      if (current === generation) state.value = result
    })
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
      settled.value = false
      state.value = null
      loadError.value = ''
      reset()
    }
  })
  watch(projectId, () => {
    if (enabled()) void load()
  })

  return { state, loadError, saving, saved, saveError, load, save }
}
