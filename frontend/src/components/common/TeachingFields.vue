<script setup lang="ts">
// 「AI 队友辅导」那几格：方式、补充要求、可读取的资料、技能。空间设置和发题页、改题页共用这一份。
//
// 技能那一格只在调用方递了候选（`skillCatalog`）时出现：发题页、改题页递，空间设置不递
// —— 那边看不到这一格，存的时候原样带着（`buildTeaching`）。
//
// 方式是四选一，每种下面一句说明它让 AI 队友怎么做。题目那一层（`inherit`）多一个
// 「沿用上层设置」：题目和空间同时生效，题目不选方式就用空间的；底下折着空间那一份，
// 写题目的要求时看得到它已经说了什么。
import type { SkillChoice } from '@/composables/useSkillCatalog'
import type { TeachingDraft } from '@/lib/teaching'
import type { SpaceMaterial, SpaceMaterialsState, SpaceTeaching, TeachingMode } from '@/types'

import { computed, reactive, useId, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import TeachingMaterialPicker from './TeachingMaterialPicker.vue'

import { buildTeaching, draftFromConfig, emptyTeachingDraft, TEACHING_MODES } from '@/lib/teaching'

const props = defineProps<{
  modelValue?: SpaceTeaching | null
  /** 这块板资料库的清单。取数在容器那边（`GET /spaces/{id}/materials`），这里只摆。 */
  materials?: SpaceMaterial[]
  materialsState?: SpaceMaterialsState
  /** 这块板「资料库」页的地址。给出去，选择器里才有那条「上传到资料库」。 */
  libraryTo?: string
  /** 题目那一层：方式可以沿用空间的，底下给出空间那一份。 */
  inherit?: boolean
  /** 空间那一份（`inherit` 时摆在底下）。 */
  spaceTeaching?: SpaceTeaching | null
  /** 能选的技能（平台发货的那几个）。不给就不画技能那一格。 */
  skillCatalog?: SkillChoice[]
  /** 技能清单没读到：已选的照旧列着，只是没有别的可加。 */
  skillCatalogFailed?: boolean
}>()
const emit = defineEmits<{ 'update:modelValue': [SpaceTeaching] }>()

const { t } = useI18n()

const draft = reactive<TeachingDraft>(emptyTeachingDraft())

/** 正把外面那一份抄进表单：这期间草稿的每一次改动都不是人敲的，别报回去。 */
let syncing = false

watch(
  () => props.modelValue,
  (config) => {
    // 跟表单现在这份一个意思就别抄回去：抄一遍会把还没成形的输入（刚敲下去的空格）
    // 就地抹掉（`buildTeaching` 会 trim）。
    if (JSON.stringify(buildTeaching(draft)) === JSON.stringify(buildTeaching(draftFromConfig(config)))) return
    syncing = true
    Object.assign(draft, draftFromConfig(config))
    syncing = false
  },
  { immediate: true, deep: true }
)

// `flush: 'sync'`：抄进来的那几次赋值要在 `syncing` 还是 true 的时候就被看见。
watch(
  draft,
  () => {
    if (!syncing) emit('update:modelValue', buildTeaching(draft))
  },
  { deep: true, flush: 'sync' }
)

/** 方式那一组：题目那一层前面多一个「沿用上层设置」（值是 null）。 */
const modes = computed<{ value: TeachingMode | null; label: string; hint: string }[]>(() => [
  ...(props.inherit
    ? [
        {
          value: null,
          label: t('spaces.teaching.mode.inherit'),
          hint: props.spaceTeaching?.mode
            ? t(`spaces.teaching.mode.${props.spaceTeaching.mode}`)
            : t('spaces.teaching.mode.inheritNone'),
        },
      ]
    : []),
  ...TEACHING_MODES.map((mode) => ({
    value: mode,
    label: t(`spaces.teaching.mode.${mode}`),
    hint: t(`spaces.teaching.mode.${mode}Hint`),
  })),
])

/** 技能的候选：清单里的，加上已经选上却不在清单里的（名字照旧列着，好让人删掉它）。 */
const skillItems = computed(() => {
  const catalog = props.skillCatalog ?? []
  const known = new Set(catalog.map((skill) => skill.name))
  return [
    ...catalog.map((skill) => ({ value: skill.name, title: skill.name, subtitle: skill.description })),
    ...draft.skills.filter((name) => !known.has(name)).map((name) => ({ value: name, title: name, subtitle: '' })),
  ]
})

const spacePrompt = computed(() => props.spaceTeaching?.systemPrompt?.trim() ?? '')

const labelId = `teaching-mode-${useId()}`
</script>

<template>
  <div class="teaching-fields">
    <div class="teaching-fields__field" data-testid="teaching-mode">
      <span :id="labelId" class="teaching-fields__label">{{ t('spaces.teaching.fields.mode') }}</span>
      <div class="teaching-fields__modes" role="radiogroup" :aria-labelledby="labelId">
        <button
          v-for="mode in modes"
          :key="mode.value ?? 'inherit'"
          type="button"
          role="radio"
          class="teaching-fields__mode"
          :class="{ 'teaching-fields__mode--on': draft.mode === mode.value }"
          :aria-checked="draft.mode === mode.value"
          @click="draft.mode = mode.value"
        >
          <span class="teaching-fields__mode-name">{{ mode.label }}</span>
          <span class="teaching-fields__mode-hint">{{ mode.hint }}</span>
        </button>
      </div>
    </div>

    <v-textarea
      v-model="draft.systemPrompt"
      rows="3"
      auto-grow
      autocomplete="off"
      variant="outlined"
      density="compact"
      hide-details="auto"
      data-testid="teaching-system-prompt"
      :label="inherit ? t('spaces.teaching.fields.taskPrompt') : t('spaces.teaching.fields.systemPrompt')"
      :placeholder="t('spaces.teaching.fields.systemPromptPlaceholder')"
    />

    <v-textarea
      v-model="draft.privatePrompt"
      rows="3"
      auto-grow
      autocomplete="off"
      variant="outlined"
      density="compact"
      hide-details="auto"
      data-testid="teaching-private-prompt"
      :label="t('spaces.teaching.fields.privatePrompt')"
      :hint="t('spaces.teaching.fields.privatePromptHint')"
      persistent-hint
    />

    <v-text-field
      v-model="draft.learningGoals"
      autocomplete="off"
      variant="outlined"
      density="compact"
      hide-details="auto"
      data-testid="teaching-learning-goals"
      :label="t('spaces.teaching.fields.learningGoals')"
      :hint="t('spaces.teaching.fields.learningGoalsHint')"
    />

    <TeachingMaterialPicker
      v-model="draft.materialIds"
      :materials="props.materials ?? []"
      :state="props.materialsState ?? 'ready'"
      :library-to="props.libraryTo"
    />

    <v-autocomplete
      v-if="skillCatalog"
      v-model="draft.skills"
      :items="skillItems"
      item-title="title"
      item-value="value"
      multiple
      chips
      closable-chips
      autocomplete="off"
      variant="outlined"
      density="compact"
      hide-details="auto"
      data-testid="teaching-skills"
      :label="t('spaces.teaching.fields.skills')"
      :hint="skillCatalogFailed ? t('spaces.teaching.fields.skillsLoadFailed') : t('spaces.teaching.fields.skillsHint')"
      persistent-hint
    >
      <template #item="{ item, props: itemProps }">
        <v-list-item v-bind="itemProps" :subtitle="item.raw.subtitle || undefined" />
      </template>
      <!-- A chosen skill keeps its description beside the id: `docx` alone does not say what it does. -->
      <template #chip="{ item, props: chipProps }">
        <v-chip v-bind="chipProps" class="teaching-fields__skill" :title="item.raw.subtitle || undefined">
          <span class="teaching-fields__skill-name">{{ item.raw.title }}</span>
          <span v-if="item.raw.subtitle" class="teaching-fields__skill-hint">{{ item.raw.subtitle }}</span>
        </v-chip>
      </template>
    </v-autocomplete>

    <details v-if="inherit && spacePrompt" class="teaching-fields__space" data-testid="teaching-space-prompt">
      <summary>{{ t('spaces.teaching.fields.spacePrompt') }}</summary>
      <p data-user-content>{{ spacePrompt }}</p>
    </details>
  </div>
</template>

<style scoped>
.teaching-fields {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.teaching-fields__field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.teaching-fields__label {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.teaching-fields__modes {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 8px;
}

.teaching-fields__mode {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 10px 12px;
  text-align: left;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  transition: border-color var(--dur-quick) var(--ease-standard);
}

.teaching-fields__mode:hover {
  border-color: var(--muted);
}

.teaching-fields__mode:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}

.teaching-fields__mode--on {
  background: var(--fill);
  border-color: var(--ink);
}

.teaching-fields__mode-name {
  color: var(--text);
}

.teaching-fields__mode-hint {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.teaching-fields__skill {
  max-width: 100%;
}

.teaching-fields__skill-name {
  flex: none;
}

.teaching-fields__skill-hint {
  min-width: 0;
  margin-left: 6px;
  overflow: hidden;
  color: var(--muted);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.teaching-fields__space {
  padding: 8px 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}

.teaching-fields__space summary {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  cursor: pointer;
}

.teaching-fields__space p {
  margin: 8px 0 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
  white-space: pre-wrap;
}
</style>
