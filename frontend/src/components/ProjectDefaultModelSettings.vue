<script setup lang="ts">
// 项目默认模型（#1365）：主线（房间聊天）用哪一档、分身用哪一档。
//
// 只吃 props、只往上发事件（`frontend_grade.py` 的 A 级）：取数与发请求在
// `composables/useProjectDefaultModelSettings.ts` 里，由设置页接线。编辑草稿（还没保存
// 的那个下拉值、分身那一档）留在这一层：那份 `state` 一换（首次取到、或保存后服务端
// 回话）草稿就归位，是这一件自己一眼看得见的事。
//
// 之前只能手改数据库，对应事故就是「agent 配了 deepseek，主线静默换到订阅 sonnet」。
import type { ProjectDefaultModel } from '@/api'

import { computed, ref, watch } from 'vue'

import { t } from '../i18n'
import { modelChoiceProps, withSaved } from '../lib/modelChoices'

import BaseButton from '@/components/base/BaseButton.vue'
import SaveStatus from '@/components/base/SaveStatus.vue'

const props = defineProps<{
  /** 读到的模型现状；还没读到是 null。 */
  state: ProjectDefaultModel | null
  /** 读失败：那一句原话。 */
  loadError: string
  /** 一次保存还在路上。 */
  saving: boolean
  /** 刚保存成功（那一行回执还亮着）。 */
  saved: boolean
  /** 保存失败：那一句原话。 */
  saveError: string
}>()

const emit = defineEmits<{ (e: 'save', model: string | null, subagent: string | null): void }>()

// 本地编辑态：用户在下拉里选了一个值但还没保存。null = 清掉显式设置（回落部署默认）。
// undefined = 没动过（跟着服务端给的走）。`state` 一换就归位，见下面的 watch。
const draft = ref<string | null | undefined>(undefined)
const subagentDraft = ref<string | null>(null)

watch(
  () => props.state,
  (state) => {
    draft.value = undefined
    subagentDraft.value = state?.subagent_model ?? null
  },
  { immediate: true }
)

const effective = computed({
  get() {
    if (!props.state) return null
    const model = draft.value !== undefined ? draft.value : props.state.model
    return model ?? props.state.deployment_default
  },
  set(model: string | null) {
    draft.value = model
  },
})

const mainItems = computed(() => (props.state ? withSaved(props.state.choices, props.state.model) : []))
const subagentItems = computed(() =>
  props.state
    ? [{ id: null, label: t('work.models.inheritMain') }, ...withSaved(props.state.choices, props.state.subagent_model)]
    : []
)

const dirty = computed(() => {
  if (!props.state) return false
  const server = props.state.model ?? null
  const now = draft.value !== undefined ? draft.value : server
  return now !== server || subagentDraft.value !== (props.state.subagent_model ?? null)
})

function save() {
  const target = draft.value !== undefined ? draft.value : props.state?.model ?? null
  emit('save', target, subagentDraft.value)
}

/** 恢复默认：清掉显式设置（发出去的是 null，回落部署默认）。 */
function resetToDeploymentDefault() {
  draft.value = null
}
</script>

<template>
  <div>
    <p class="t-body c-muted mb-4">{{ t('work.models.description') }}</p>
    <v-alert v-if="loadError" type="error" variant="tonal" class="mb-3">{{ loadError }}</v-alert>
    <template v-if="state">
      <div class="d-flex flex-column" style="gap: 24px; max-width: 480px">
        <v-select
          v-model="effective"
          :items="mainItems"
          item-title="label"
          item-value="id"
          :item-props="modelChoiceProps"
          :disabled="saving"
          autocomplete="off"
          density="compact"
          variant="outlined"
          hide-details
          :label="t('work.models.main')"
        />
        <v-select
          v-model="subagentDraft"
          :items="subagentItems"
          item-title="label"
          item-value="id"
          :item-props="modelChoiceProps"
          :disabled="saving"
          autocomplete="off"
          density="compact"
          variant="outlined"
          :label="t('work.models.subagent')"
          :hint="t('work.models.subagentHint')"
          persistent-hint
        />
        <div class="d-flex align-center" style="gap: 12px">
          <BaseButton kind="primary" size="sm" :disabled="saving || !dirty" @click="save">
            {{ t('work.projectSettings.defaultModelBlock.save') }}
          </BaseButton>
          <BaseButton
            v-if="state.model !== null"
            kind="secondary"
            size="sm"
            :disabled="saving"
            @click="resetToDeploymentDefault"
          >
            {{ t('work.projectSettings.defaultModelBlock.reset') }}
          </BaseButton>
          <SaveStatus :saving="saving" :saved="saved" :error="saveError" />
        </div>
      </div>
    </template>
  </div>
</template>
