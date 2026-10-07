// 项目设置页「默认模型」这一节的数据：主线（房间聊天）和子 agent 各用哪个模型。
//
// 拆自 `components/ProjectDefaultModelSettings.vue`：取数、本地草稿、保存都在这一层，
// 组件只画。草稿和已保存的值比出来的 `dirty` 交给 `useSaveState`，保存按钮和
// `SaveStatus` 就跟着它走。
//
// `draft === null` 是「清掉显式设置，回落部署默认」，和「没动过」（`undefined`）不是
// 一回事：前者要写回一次，后者不该进 dirty。
import type { ProjectDefaultModel } from '@/api'

import { computed, ref } from 'vue'

import { useSaveState } from '@/composables/useSaveState'

import { getProjectDefaultModel, setProjectDefaultModel } from '@/api'
import { t } from '@/i18n'
import { withSaved } from '@/lib/modelChoices'

export function useProjectDefaultModel(projectId: () => string) {
  const state = ref<ProjectDefaultModel | null>(null)
  const loadError = ref('')

  // 本地编辑态：用户在下拉里选了一个值但还没保存。
  const draft = ref<string | null | undefined>(undefined)
  const subagentDraft = ref<string | null>(null)

  const effective = computed({
    get() {
      if (!state.value) return null
      const model = draft.value !== undefined ? draft.value : state.value.model
      return model ?? state.value.deployment_default
    },
    set(model: string | null) {
      draft.value = model
    },
  })

  const mainItems = computed(() => (state.value ? withSaved(state.value.choices, state.value.model) : []))
  const subagentItems = computed(() =>
    state.value
      ? [
          { id: null, label: t('work.models.inheritMain') },
          ...withSaved(state.value.choices, state.value.subagent_model),
        ]
      : []
  )

  const dirty = computed(() => {
    if (!state.value) return false
    const server = state.value.model ?? null
    const now = draft.value !== undefined ? draft.value : server
    return now !== server || subagentDraft.value !== (state.value.subagent_model ?? null)
  })

  // 保存结果就地回执（§3.11）：下拉旁边那一行，不再只靠按钮转圈。
  const {
    saving,
    saved,
    error: saveError,
    run,
  } = useSaveState({
    feedback: 'inline',
    dirty: () => dirty.value,
    messages: { failed: t('work.projectSettings.defaultModelBlock.saveFailed') },
  })

  async function load() {
    loadError.value = ''
    try {
      state.value = await getProjectDefaultModel(projectId())
      subagentDraft.value = state.value.subagent_model ?? null
      draft.value = undefined
    } catch (e) {
      loadError.value = e instanceof Error ? e.message : t('work.projectSettings.defaultModelBlock.loadFailed')
    }
  }

  async function save() {
    if (!state.value?.can_manage) return
    const target = draft.value !== undefined ? draft.value : state.value.model
    await run(async () => {
      state.value = await setProjectDefaultModel(projectId(), target, subagentDraft.value)
      draft.value = undefined
    })
  }

  async function resetToDeploymentDefault() {
    if (!state.value?.can_manage) return
    draft.value = null
  }

  return {
    state,
    loadError,
    effective,
    subagentDraft,
    mainItems,
    subagentItems,
    dirty,
    saving,
    saved,
    saveError,
    load,
    save,
    resetToDeploymentDefault,
  }
}
