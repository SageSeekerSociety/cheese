<script setup lang="ts">
import type { AgentConfiguration, AgentType, ProjectAgent } from '../../cx_types'
import type { AgentFieldChoice } from '../../lib/modelChoices'
import type { AgentEffort } from '../../lib/projectAgents'

import { computed, ref, toRaw, watch } from 'vue'

import { createProjectAgent, getProjectDefaultModel, updateProjectAgent } from '../../api'
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
const saving = ref(false)
const error = ref<string | null>(null)
const submitted = ref(false)
const choices = ref<AgentFieldChoice[]>([])
const modelsLoading = ref(false)
// Advanced settings change what every turn costs or can do, so they are a
// project manager's, as the project main model is; the backend refuses the
// rest (`projects_agents.py`).
const canManage = ref(false)
const advancedOpen = ref(false)
const allowMax = ref(false)
const customCompact = ref(false)
const compactPercent = ref(80)

function blankConfiguration(): AgentConfiguration {
  return { body: '', skills: [], model: null, effort: null, compact_percent: null }
}

async function loadModels() {
  modelsLoading.value = true
  choices.value = []
  try {
    const state = await getProjectDefaultModel(props.projectId)
    choices.value = state.choices
    canManage.value = state.can_manage === true
  } catch (e) {
    error.value = e instanceof Error ? e.message : t('work.models.loadError')
  } finally {
    modelsLoading.value = false
  }
}
const presetItems = computed(() => [
  { title: t('work.projectSettings.agents.editor.presetCustom'), value: null },
  ...props.types.map((preset) => ({ title: preset.title || preset.name, value: preset.name })),
])
const nameProblem = computed(() => displayNameError(displayName.value))
const handleProblem = computed(() => (isNew.value ? handleError(handle.value.trim()) : null))

// The model a teammate that names none runs on: the project's.
const projectModel = computed(() => choices.value.find((choice) => choice.default) ?? null)
const modelItems = computed(() => [
  {
    id: null,
    label: projectModel.value
      ? t('work.projectSettings.agents.editor.modelInherit', { model: projectModel.value.label })
      : t('work.projectSettings.agents.editor.modelInheritPlain'),
    allowed: true,
    requires_plan: null,
  },
  ...choices.value,
])
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
const effortHint = computed(() => {
  if (honoured.value.size === 0) return t('work.projectSettings.agents.editor.effortNone')
  const effort = draft.value.effort
  if (effort && !honoured.value.has(effort))
    return t('work.projectSettings.agents.editor.effortUnsupported', { effort: effortLabel(effort) })
  return effort ? '' : t('work.projectSettings.agents.editor.effortHintAuto')
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
    error.value = null
    advancedOpen.value = false
    displayName.value = props.agent ? teammateName(props.agent.display_name, props.agent.name_source) : ''
    shownName.value = displayName.value
    handle.value = props.agent?.handle ?? ''
    presetName.value = null
    draft.value = blankConfiguration()
    void loadModels()
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

async function save() {
  submitted.value = true
  if (nameProblem.value || handleProblem.value) return
  if (draft.value.model && !choices.value.some((choice) => choice.id === draft.value.model && choice.allowed)) {
    error.value = t('work.models.chooseAvailable')
    return
  }
  saving.value = true
  error.value = null
  try {
    const payload = {
      display_name: displayName.value.trim(),
      configuration: {
        ...draft.value,
        model: draft.value.model ?? null,
        effort: draft.value.effort ?? null,
        compact_percent: customCompact.value ? compactPercent.value : null,
      },
    }
    // A name left as it was shown stays unset: saving the default teammate's
    // settings must not turn the name it is shown by into one somebody chose.
    if (props.agent?.id)
      await updateProjectAgent(
        props.projectId,
        props.agent.id,
        payload.display_name === shownName.value ? { configuration: payload.configuration } : payload
      )
    else
      await createProjectAgent(props.projectId, {
        ...payload,
        handle: handle.value.trim() || undefined,
        type_name: presetName.value,
      })
    emit('saved')
    close()
  } catch (e) {
    error.value = e instanceof Error ? e.message : t('work.projectSettings.agents.editor.saveFailed')
  } finally {
    saving.value = false
  }
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
    @primary="save"
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
        <p v-if="!canManage" class="t-meta c-muted mb-3">
          {{ t('work.projectSettings.agents.editor.advancedReadOnly') }}
        </p>
        <v-checkbox
          v-if="honoured.has('max') || draft.effort === 'max'"
          v-model="allowMax"
          :label="t('work.projectSettings.agents.editor.allowMax')"
          :disabled="!canManage"
          hide-details
          density="compact"
        />

        <div class="t-label mt-3 mb-1">{{ t('work.projectSettings.agents.editor.compact') }}</div>
        <v-checkbox
          v-model="customCompact"
          :label="t('work.projectSettings.agents.editor.compactCustom')"
          :disabled="!canManage"
          hide-details
          density="compact"
        />
        <div v-if="customCompact" class="mb-2">
          <v-slider
            v-model="compactPercent"
            :min="50"
            :max="90"
            :step="5"
            :disabled="!canManage"
            :label="t('work.projectSettings.agents.editor.compactValue', { percent: compactPercent })"
            hide-details
            color="primary"
          />
          <p class="t-meta c-muted mt-1 mb-0">{{ t('work.projectSettings.agents.editor.compactHint') }}</p>
        </div>

        <v-combobox
          v-model="draft.skills"
          multiple
          chips
          closable-chips
          :items="[]"
          :label="t('work.projectSettings.agents.editor.skills')"
          :hint="t('work.projectSettings.agents.editor.skillsHint')"
          persistent-hint
          :disabled="!canManage"
          variant="outlined"
          class="mt-4"
          autocomplete="off"
        />

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
