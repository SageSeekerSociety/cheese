<script setup lang="ts">
import type { AgentTypeOptions } from '../../api'
import type { AgentConfiguration, AgentType, ProjectAgent } from '../../cx_types'

import { computed, ref, toRaw, watch } from 'vue'

import { createProjectAgent, getProjectAgentOptions, updateProjectAgent } from '../../api'
import { displayNameError, fieldChoices, handleError } from '../../lib/projectAgents'

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
const draft = ref<AgentConfiguration>({
  body: '',
  model: '',
  harness: 'claude-code',
  skills: [],
  mcp_servers: [],
  effort: null,
})
const options = ref<AgentTypeOptions>({})
const loading = ref(false)
const saving = ref(false)
const error = ref<string | null>(null)
const submitted = ref(false)
const modelItems = computed(() => fieldChoices(options.value, 'model'))
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
    model: preset?.model || modelItems.value.find((item) => item.default)?.id || '',
    harness: preset?.harness || 'claude-code',
    skills: [...(preset?.skills ?? [])],
    mcp_servers: [...(preset?.mcp_servers ?? [])],
    effort: preset?.effort ?? null,
  }
}

watch(
  () => [props.modelValue, props.agent, props.projectId] as const,
  async ([open], _, onCleanup) => {
    if (!open) return
    let active = true
    onCleanup(() => {
      active = false
    })
    submitted.value = false
    error.value = null
    loading.value = true
    options.value = {}
    displayName.value = props.agent?.display_name ?? ''
    handle.value = props.agent?.handle ?? ''
    presetName.value = null
    if (props.agent) draft.value = structuredClone(toRaw(props.agent.configuration))
    else applyPreset(null)
    try {
      const result = await getProjectAgentOptions(props.projectId)
      if (!active) return
      options.value = result
      if (!modelItems.value.length) error.value = result.model?.reason || '当前项目没有可用模型，请检查模型服务'
      if (isNew.value) applyPreset(presetName.value)
    } catch (e) {
      if (active) error.value = e instanceof Error ? e.message : '加载模型选项失败'
    } finally {
      if (active) loading.value = false
    }
  },
  { immediate: true }
)

function close() {
  emit('update:modelValue', false)
}

async function save() {
  submitted.value = true
  if (nameProblem.value || handleProblem.value || loading.value) return
  if (!modelItems.value.some((item) => item.id === draft.value.model)) {
    error.value = '请选择当前项目可用的模型'
    return
  }
  saving.value = true
  error.value = null
  try {
    const payload = { display_name: displayName.value.trim(), configuration: draft.value }
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
  <v-dialog
    :model-value="modelValue"
    max-width="720"
    scrollable
    @update:model-value="emit('update:modelValue', $event)"
  >
    <v-card>
      <v-card-title class="d-flex align-center pa-5 pb-3">
        <span class="t-title">{{ isNew ? '新建 AI 队友' : '修改 AI 队友' }}</span>
        <v-spacer />
        <v-btn variant="text" icon="mdi-close" size="small" aria-label="关闭" @click="close" />
      </v-card-title>
      <v-card-text class="pa-5 pt-0">
        <v-alert v-if="error" type="error" density="comfortable" class="mb-4">{{ error }}</v-alert>
        <v-text-field
          v-model="displayName"
          label="名字"
          variant="outlined"
          :error-messages="submitted && nameProblem ? [nameProblem] : []"
        />
        <v-text-field
          v-if="isNew"
          v-model="handle"
          label="标识（英文，可留空）"
          variant="outlined"
          :error-messages="submitted && handleProblem ? [handleProblem] : []"
        />
        <div v-else class="t-meta c-muted mb-4">标识 · {{ agent?.handle }}</div>
        <v-select
          v-if="isNew"
          v-model="presetName"
          :items="presetItems"
          label="从内置配置开始"
          variant="outlined"
          :disabled="loading"
          @update:model-value="applyPreset"
        />
        <v-textarea v-model="draft.body" label="角色设定（可留空）" rows="6" variant="outlined" />
        <v-select
          v-model="draft.model"
          :items="modelItems"
          item-title="label"
          item-value="id"
          label="模型"
          variant="outlined"
          :loading="loading"
          :disabled="loading"
        />
        <p v-if="!isNew" class="t-meta c-muted">修改只影响这个队友，从下一轮开始生效，已有记忆保留</p>
      </v-card-text>
      <v-card-actions class="pa-5 pt-0">
        <v-spacer />
        <v-btn variant="text" @click="close">取消</v-btn>
        <v-btn color="primary" variant="flat" :loading="saving" :disabled="loading || !modelItems.length" @click="save"
          >保存</v-btn
        >
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
