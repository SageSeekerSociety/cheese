<script setup lang="ts">
// 「AI 队友」的编辑器。它编辑的是一个**角色**：名字、标识、人设。
//
// 模型和运行方式曾经也在这里，是两个下拉菜单。它们走了，因为它们不是这个队友
// 的属性：模型绑在活上（结论 3），卡是用户接触模型的唯一地方；运行方式是部署的
// 开发者选项（结论 28），普通用户看不见。留着那两个菜单，等于让人在这里挑一个
// 之后没有任何一处会去读的值。
import type { AgentConfiguration, AgentType, ProjectAgent } from '../../cx_types'

import { computed, ref, toRaw, watch } from 'vue'

import { createProjectAgent, updateProjectAgent } from '../../api'
import { displayNameError, handleError } from '../../lib/projectAgents'

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
const draft = ref<AgentConfiguration>({ body: '', skills: [], mcp_servers: [] })
const saving = ref(false)
const error = ref<string | null>(null)
const submitted = ref(false)
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
    mcp_servers: [...(preset?.mcp_servers ?? [])],
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
      <!-- pt-2, not pt-0: in a `scrollable` dialog THIS element is the scroller
           (VDialog.sass: `.v-dialog--scrollable > ... > .v-card-text { overflow-y:
           auto }`), and an outlined field's floating label is `translateY(-50%)`
           on its own top border — ~8px of it sits above the field's box. At
           padding-top 0 the scroller clipped the top half of 「名字」, and pt-2
           lands the label exactly ON the clip edge, so it needs the next step
           up rather than the exact 8px. -->
      <v-card-text class="pa-5 pt-3">
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
        <p v-if="!isNew" class="t-meta c-muted">修改只影响这个队友，从下一轮开始生效，已有记忆保留</p>
      </v-card-text>
      <v-card-actions class="pa-5 pt-0">
        <v-spacer />
        <v-btn variant="text" @click="close">取消</v-btn>
        <v-btn color="primary" variant="flat" :loading="saving" @click="save">保存</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
