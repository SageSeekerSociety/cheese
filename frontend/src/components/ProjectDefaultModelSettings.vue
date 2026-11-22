<script setup lang="ts">
// 项目主模型（#1365）：没单独设置模型的 AI 队友用它。不设就是「自动」：对话、任务、分身
// 各用平台指的模型，下拉第一项把这三个写出来。
//
// 只吃 props、只往上发事件（`frontend_grade.py` 的 A 级）：取数与发请求在
// `composables/useProjectDefaultModelSettings.ts` 里，由设置页接线。编辑草稿（还没保存
// 的那个下拉值）留在这一层：那份 `state` 一换（首次取到、或保存后服务端回话）草稿就
// 归位，是这一件自己一眼看得见的事。
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

const emit = defineEmits<{ (e: 'save', model: string | null): void }>()

// 本地编辑态：用户在下拉里选了一个值但还没保存。null = 「自动」（清掉项目主模型）。
// undefined = 没动过（跟着服务端给的走）。`state` 一换就归位，见下面的 watch。
const draft = ref<string | null | undefined>(undefined)

watch(
  () => props.state,
  () => {
    draft.value = undefined
  },
  { immediate: true }
)

const selected = computed({
  get() {
    if (!props.state) return null
    return draft.value !== undefined ? draft.value : props.state.model
  },
  set(model: string | null) {
    draft.value = model
  },
})

/** 「自动」那一项：写明三个场合此刻各用哪个模型。 */
const automaticLabel = computed(() => {
  const automatic = props.state?.automatic
  if (!automatic?.conversation || !automatic.task || !automatic.subagent) return t('work.models.automaticPlain')
  return t('work.models.automatic', {
    conversation: automatic.conversation.label,
    task: automatic.task.label,
    subagent: automatic.subagent.label,
  })
})

const items = computed(() =>
  props.state ? [{ id: null, label: automaticLabel.value }, ...withSaved(props.state.choices, props.state.model)] : []
)

const dirty = computed(() => {
  if (!props.state) return false
  return draft.value !== undefined && draft.value !== (props.state.model ?? null)
})

function save() {
  emit('save', selected.value)
}

/** 改回自动：清掉项目主模型（发出去的是 null）。 */
function resetToAutomatic() {
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
          v-model="selected"
          :items="items"
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
        <div class="d-flex align-center" style="gap: 12px">
          <BaseButton kind="primary" size="sm" :disabled="saving || !dirty" @click="save">
            {{ t('work.projectSettings.defaultModelBlock.save') }}
          </BaseButton>
          <BaseButton
            v-if="state.model !== null"
            kind="secondary"
            size="sm"
            :disabled="saving"
            @click="resetToAutomatic"
          >
            {{ t('work.projectSettings.defaultModelBlock.reset') }}
          </BaseButton>
          <SaveStatus :saving="saving" :saved="saved" :error="saveError" />
        </div>
      </div>
    </template>
  </div>
</template>
