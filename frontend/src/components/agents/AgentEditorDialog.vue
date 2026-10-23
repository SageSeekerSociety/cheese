<script setup lang="ts">
import type { AgentConfiguration, AgentType, ProjectAgent } from '../../cx_types'
import type { AgentEffort } from '../../lib/projectAgents'

import { computed, ref, toRaw, watch } from 'vue'

import { useAgentEditor } from '../../composables/useAgentEditor'
import { t } from '../../i18n'
import { teammateName } from '../../lib/agentNames'
import { modelChoiceProps } from '../../lib/modelChoices'
import { displayNameError, EFFORT_LEVELS, effortLabel, handleError } from '../../lib/projectAgents'
import AdaptiveDialog from '../common/AdaptiveDialog.vue'
import SegmentedControl from '../common/SegmentedControl.vue'

const props = defineProps<{
  modelValue: boolean
  projectId: string
  agent: ProjectAgent | null
  types: AgentType[]
}>()
const emit = defineEmits<{ 'update:modelValue': [boolean]; saved: [] }>()
const isNew = computed(() => props.agent === null)
const displayName = ref('')
// The name the field opened with. Saved unchanged, it is not sent.
const shownName = ref('')
const handle = ref('')
const presetName = ref<string | null>(null)
const draft = ref<AgentConfiguration>(blankConfiguration())
const submitted = ref(false)
const advancedOpen = ref(false)
const allowMax = ref(false)
const customCompact = ref(false)
const compactPercent = ref(80)

// 模型目录和「新建 / 更新」那两下都在这里，组件只画和接线
// （`.claude/rules/architecture.md`：`src/components/**` 不碰 API 层）。
const { choices, connectionModel, modelsLoading, saving, error, clearError, loadModels, save } = useAgentEditor(
  () => props.projectId
)

function blankConfiguration(): AgentConfiguration {
  return { body: '', skills: [], model: null, effort: null, compact_percent: null }
}

const presetItems = computed(() => [
  { title: t('work.projectSettings.agents.editor.presetCustom'), value: null },
  ...props.types.map((preset) => ({ title: preset.title || preset.name, value: preset.name })),
])
const nameProblem = computed(() => displayNameError(displayName.value))
const handleProblem = computed(() => (isNew.value ? handleError(handle.value.trim()) : null))

// The model a teammate that names none runs on: the project's.
const projectModel = computed(() => choices.value.find((choice) => choice.default) ?? null)
const modelItems = computed(() =>
  connectionModel.value
    ? choices.value
    : [
        {
          id: null,
          label: projectModel.value
            ? t('work.projectSettings.agents.editor.modelInherit', { model: projectModel.value.label })
            : t('work.projectSettings.agents.editor.modelInheritPlain'),
          allowed: true,
          requires_plan: null,
        },
        ...choices.value,
      ]
)
const runsOn = computed(() =>
  draft.value.model ? choices.value.find((choice) => choice.id === draft.value.model) ?? null : projectModel.value
)
const honoured = computed(() => new Set(runsOn.value?.efforts ?? []))
const effortButtons = computed(() => {
  const levels: AgentEffort[] = EFFORT_LEVELS.filter((level) => level !== 'max')
  if (allowMax.value || draft.value.effort === 'max') levels.push('max')
  return levels
})
const effortOptions = computed(() => [
  { value: 'auto' as const, label: t('work.projectSettings.agents.editor.effortAuto') },
  ...effortButtons.value.map((level) => ({
    value: level,
    label: effortLabel(level),
    disabled: !honoured.value.has(level) && draft.value.effort !== level,
    title: honoured.value.has(level) ? undefined : t('work.projectSettings.agents.editor.effortLevelUnsupported'),
  })),
])
// 点不了的那几档为什么点不了，写在下面：灰掉的按钮上的 title 提示很多浏览器不显示，
// 只看见一档灰着，不知道为什么。
const unsupportedLevels = computed(() => effortButtons.value.filter((level) => !honoured.value.has(level)))
const effortHint = computed(() => {
  if (honoured.value.size === 0) return t('work.projectSettings.agents.editor.effortNone')
  const effort = draft.value.effort
  if (effort && !honoured.value.has(effort))
    return t('work.projectSettings.agents.editor.effortUnsupported', { effort: effortLabel(effort) })
  const missing = unsupportedLevels.value.length
    ? t('work.projectSettings.agents.editor.effortLevelsUnsupported', {
        levels: unsupportedLevels.value.map(effortLabel).join(t('work.room.roster.listSeparator')),
      })
    : ''
  return [effort ? '' : t('work.projectSettings.agents.editor.effortHintAuto'), missing].filter(Boolean).join(' ')
})

function applyPreset(name: string | null) {
  const preset = props.types.find((item) => item.name === name)
  draft.value = {
    ...draft.value,
    body: preset?.body ?? '',
    skills: [...(preset?.skills ?? [])],
  }
}

watch(
  () => [props.modelValue, props.agent] as const,
  ([open]) => {
    if (!open) return
    submitted.value = false
    clearError()
    advancedOpen.value = false
    displayName.value = props.agent ? teammateName(props.agent.display_name, props.agent.name_source) : ''
    shownName.value = displayName.value
    handle.value = props.agent?.handle ?? ''
    presetName.value = null
    draft.value = blankConfiguration()
    void loadModels(props.agent)
    if (props.agent) draft.value = { ...blankConfiguration(), ...structuredClone(toRaw(props.agent.configuration)) }
    else applyPreset(null)
    allowMax.value = draft.value.effort === 'max'
    customCompact.value = draft.value.compact_percent != null
    compactPercent.value = draft.value.compact_percent ?? 80
  },
  { immediate: true }
)

watch(allowMax, (allowed) => {
  if (!allowed && draft.value.effort === 'max') draft.value.effort = null
})

function chooseEffort(value: AgentEffort | 'auto') {
  draft.value.effort = value === 'auto' ? null : value
}

function close() {
  emit('update:modelValue', false)
}

async function submit() {
  submitted.value = true
  if (nameProblem.value || handleProblem.value) return
  if (
    (connectionModel.value || draft.value.model) &&
    !choices.value.some((choice) => choice.id === draft.value.model && choice.allowed)
  ) {
    error.value = t('work.models.chooseAvailable')
    return
  }
  const saved = await save(props.agent, {
    display_name: displayName.value.trim(),
    configuration: {
      ...draft.value,
      model: draft.value.model ?? null,
      effort: draft.value.effort ?? null,
      compact_percent: customCompact.value ? compactPercent.value : null,
    },
    // A name left as it was shown stays unset: saving the default teammate's
    // settings must not turn the name it is shown by into one somebody chose.
    nameUnchanged: displayName.value.trim() === shownName.value,
    handle: handle.value.trim() || undefined,
    type_name: presetName.value,
  })
  if (!saved) return
  emit('saved')
  close()
}
</script>

<template>
  <!-- One long form: a dialog on desktop, a full page on phones (save stays in the header, above the keyboard). -->
  <AdaptiveDialog
    :model-value="modelValue"
    :title="
      isNew ? t('work.projectSettings.agents.editor.titleNew') : t('work.projectSettings.agents.editor.titleEdit')
    "
    :primary-label="t('work.projectSettings.agents.editor.save')"
    :primary-loading="saving"
    :max-width="720"
    @update:model-value="emit('update:modelValue', $event)"
    @primary="submit"
  >
    <v-alert v-if="error" type="error" density="comfortable" class="mb-4">{{ error }}</v-alert>
    <v-text-field
      v-model="displayName"
      autocomplete="off"
      :label="t('work.projectSettings.agents.editor.name')"
      variant="outlined"
      :error-messages="submitted && nameProblem ? [nameProblem] : []"
    />
    <v-text-field
      v-if="isNew"
      v-model="handle"
      autocomplete="off"
      :label="t('work.projectSettings.agents.editor.handle')"
      variant="outlined"
      :error-messages="submitted && handleProblem ? [handleProblem] : []"
    />
    <v-select
      v-if="isNew"
      v-model="presetName"
      autocomplete="off"
      :items="presetItems"
      :label="t('work.projectSettings.agents.editor.preset')"
      variant="outlined"
      @update:model-value="applyPreset"
    />
    <v-textarea
      v-model="draft.body"
      autocomplete="off"
      :label="t('work.projectSettings.agents.editor.body')"
      rows="6"
      variant="outlined"
    />
    <v-select
      v-model="draft.model"
      :items="modelItems"
      item-title="label"
      item-value="id"
      :item-props="modelChoiceProps"
      :label="t('work.projectSettings.agents.editor.model')"
      :loading="modelsLoading"
      :disabled="modelsLoading || saving"
      variant="outlined"
      autocomplete="off"
    />

    <div class="t-label mb-2">{{ t('work.projectSettings.agents.editor.effort') }}</div>
    <SegmentedControl
      :model-value="draft.effort ?? 'auto'"
      :options="effortOptions"
      :label="t('work.projectSettings.agents.editor.effort')"
      size="md"
      @update:model-value="chooseEffort"
    />
    <p v-if="effortHint" class="t-meta c-muted mt-2 mb-0">{{ effortHint }}</p>

    <button
      type="button"
      class="advanced-toggle mt-4"
      :aria-expanded="advancedOpen"
      @click="advancedOpen = !advancedOpen"
    >
      <v-icon size="16">{{ advancedOpen ? 'mdi-chevron-down' : 'mdi-chevron-right' }}</v-icon>
      {{ t('work.projectSettings.agents.editor.advanced') }}
    </button>
    <v-expand-transition>
      <div v-if="advancedOpen" class="advanced">
        <v-checkbox
          v-if="honoured.has('max') || draft.effort === 'max'"
          v-model="allowMax"
          :label="t('work.projectSettings.agents.editor.allowMax')"
          hide-details
          density="compact"
        />

        <div class="t-label mt-3 mb-1">{{ t('work.projectSettings.agents.editor.compact') }}</div>
        <v-checkbox
          v-model="customCompact"
          :label="t('work.projectSettings.agents.editor.compactCustom')"
          hide-details
          density="compact"
        />
        <div v-if="customCompact" class="mb-2">
          <v-slider
            v-model="compactPercent"
            :min="50"
            :max="90"
            :step="5"
            :label="t('work.projectSettings.agents.editor.compactValue', { percent: compactPercent })"
            hide-details
            color="primary"
          />
          <p class="t-meta c-muted mt-1 mb-0">{{ t('work.projectSettings.agents.editor.compactHint') }}</p>
        </div>

        <div v-if="!isNew" class="t-meta c-muted mt-4">
          {{ t('work.projectSettings.agents.editor.handleReadonly', { handle: agent?.handle }) }}
        </div>
      </div>
    </v-expand-transition>
    <p v-if="!isNew" class="t-meta c-muted mt-4 mb-0">{{ t('work.projectSettings.agents.editor.editHint') }}</p>
  </AdaptiveDialog>
</template>

<style scoped>
/* Collapsed by default: it reads as a quiet link, not as another field. */
.advanced-toggle {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 0;
  font-size: 13px;
  color: var(--muted);
  background: transparent;
  border: 0;
  cursor: pointer;
}
.advanced-toggle:hover {
  color: var(--text);
}
.advanced {
  border-top: 1px dashed var(--line);
  margin-top: 8px;
  padding-top: 12px;
}
</style>
