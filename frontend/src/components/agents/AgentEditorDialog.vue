<script setup lang="ts">
import type { AgentFieldChoice } from '../../api'
import type { AgentConfiguration, AgentType, ProjectAgent } from '../../cx_types'

import { computed, ref, toRaw, watch } from 'vue'

import { createProjectAgent, getProjectDefaultModel, updateProjectAgent } from '../../api'
import { t } from '../../i18n'
import { displayNameError, handleError } from '../../lib/projectAgents'
import AdaptiveDialog from '../common/AdaptiveDialog.vue'

const props = defineProps<{
  modelValue: boolean
  projectId: string
  agent: ProjectAgent | null
  types: AgentType[]
}>()
const emit = defineEmits<{ 'update:modelValue': [boolean]; saved: [] }>()
const isNew = computed(() => props.agent === null)
const displayName = ref('')
const handle = ref('')
const presetName = ref<string | null>(null)
const draft = ref<AgentConfiguration>({ body: '', skills: [] })
const saving = ref(false)
const error = ref<string | null>(null)
const submitted = ref(false)
const specifyModel = ref(false)
const choices = ref<AgentFieldChoice[]>([])
const modelsLoading = ref(false)

async function loadModels() {
  modelsLoading.value = true
  choices.value = []
  try {
    choices.value = (await getProjectDefaultModel(props.projectId)).choices
  } catch (e) {
    error.value = e instanceof Error ? e.message : t('work.models.loadError')
  } finally {
    modelsLoading.value = false
  }
}
const presetItems = computed(() => [
  { title: '自行填写', value: null },
  ...props.types.map((preset) => ({ title: preset.title || preset.name, value: preset.name })),
])
const nameProblem = computed(() => displayNameError(displayName.value))
const handleProblem = computed(() => (isNew.value ? handleError(handle.value.trim()) : null))

function applyPreset(name: string | null) {
  const preset = props.types.find((item) => item.name === name)
  draft.value = {
    body: preset?.body ?? '',
    skills: [...(preset?.skills ?? [])],
    model: draft.value.model ?? null,
  }
}

watch(
  () => [props.modelValue, props.agent] as const,
  ([open]) => {
    if (!open) return
    submitted.value = false
    error.value = null
    displayName.value = props.agent?.display_name ?? ''
    handle.value = props.agent?.handle ?? ''
    presetName.value = null
    specifyModel.value = !!props.agent?.configuration.model
    draft.value.model = null
    void loadModels()
    if (props.agent) draft.value = structuredClone(toRaw(props.agent.configuration))
    else applyPreset(null)
  },
  { immediate: true }
)

function close() {
  emit('update:modelValue', false)
}

async function save() {
  submitted.value = true
  if (nameProblem.value || handleProblem.value) return
  if (specifyModel.value && !choices.value.some((choice) => choice.id === draft.value.model)) {
    error.value = t('work.models.chooseAvailable')
    return
  }
  saving.value = true
  error.value = null
  try {
    const payload = {
      display_name: displayName.value.trim(),
      configuration: { ...draft.value, model: specifyModel.value ? draft.value.model : null },
    }
    if (props.agent?.id) await updateProjectAgent(props.projectId, props.agent.id, payload)
    else
      await createProjectAgent(props.projectId, {
        ...payload,
        handle: handle.value.trim() || undefined,
        type_name: presetName.value,
      })
    emit('saved')
    close()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '保存失败'
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <!-- 一张长表单：桌面上是对话框，手机上是整页（保存在页头右边，不会被键盘盖住）。 -->
  <AdaptiveDialog
    :model-value="modelValue"
    :title="isNew ? '新建 AI 队友' : '修改 AI 队友'"
    primary-label="保存"
    :primary-loading="saving"
    :max-width="720"
    @update:model-value="emit('update:modelValue', $event)"
    @primary="save"
  >
    <v-alert v-if="error" type="error" density="comfortable" class="mb-4">{{ error }}</v-alert>
    <v-text-field
      v-model="displayName"
      autocomplete="off"
      label="名字"
      variant="outlined"
      :error-messages="submitted && nameProblem ? [nameProblem] : []"
    />
    <v-text-field
      v-if="isNew"
      v-model="handle"
      autocomplete="off"
      label="标识（英文，可留空）"
      variant="outlined"
      :error-messages="submitted && handleProblem ? [handleProblem] : []"
    />
    <div v-else class="t-meta c-muted mb-4">标识 · {{ agent?.handle }}</div>
    <v-select
      v-if="isNew"
      v-model="presetName"
      autocomplete="off"
      :items="presetItems"
      label="从内置配置开始"
      variant="outlined"
      @update:model-value="applyPreset"
    />
    <v-textarea v-model="draft.body" autocomplete="off" label="角色设定（可留空）" rows="6" variant="outlined" />
    <v-checkbox v-model="specifyModel" :label="t('work.models.assign')" hide-details />
    <v-select
      v-if="specifyModel"
      v-model="draft.model"
      :items="choices"
      item-title="label"
      item-value="id"
      :label="t('work.models.agent')"
      :loading="modelsLoading"
      :disabled="modelsLoading || saving"
      variant="outlined"
      class="mt-4"
      autocomplete="off"
    />
    <p v-else class="t-meta c-muted mb-4">{{ t('work.models.inheritHint') }}</p>
    <p v-if="!isNew" class="t-meta c-muted">修改只影响这个队友，下次运行时生效，已有记忆保留</p>
  </AdaptiveDialog>
</template>
