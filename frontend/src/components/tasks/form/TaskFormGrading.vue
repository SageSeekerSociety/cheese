<script setup lang="ts">
// 「提交与评分」那一节：学生交哪几样东西（每样是文件还是文字），评分要点（评什么、
// 多少分、怎样算做到），以及评分要点给不给学生看。
//
// 它只画：三样值都是 props 进来的，改一次往上报一次。题目分了阶段（`stages`）时，提交
// 项和评分要点按阶段分组，每组下面加的那一项属于这个阶段；不分阶段时就是一组，没有
// 阶段的标题。空着的评分行在提交时由 `useTaskForm` 去掉；写了标准没写名称的那一行，外面
// 用 `missingName` 让它标红。
import type { TaskRubricLine, TaskStage, TaskSubmissionEntryType, TaskSubmissionSchemaEntry } from '@/types'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import TaskFormSection from './TaskFormSection.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseField from '@/components/base/BaseField.vue'
import SegmentedControl from '@/components/common/SegmentedControl.vue'
import { settledStage } from '@/lib/taskStages'

const props = withDefaults(
  defineProps<{
    /** 点过发布后，有写了标准却没写名称的那一行。 */
    missingName?: boolean
    /** 题目的阶段；空 = 不分阶段。 */
    stages?: TaskStage[]
  }>(),
  { missingName: false, stages: () => [] }
)

const items = defineModel<TaskSubmissionSchemaEntry[]>('submissionSchema', { required: true })
const rubric = defineModel<TaskRubricLine[]>('rubric', { required: true })
const rubricPublic = defineModel<boolean>('rubricPublic', { required: true })

const { t } = useI18n()

const typeOptions = computed(() => [
  { value: 'FILE' as const, label: t('tasks.form.grading.typeFile') },
  { value: 'TEXT' as const, label: t('tasks.form.grading.typeText') },
])

/** 每组：一个阶段（不分阶段时是唯一的那一组，没有标题，阶段是 null），和按阶段的 id
 *  归到它下面的提交项、评分要点在整份列表里的位置。 */
const groups = computed(() => {
  const stages = props.stages.length ? props.stages : [null]
  return stages.map((stage, index) => {
    const id = stage?.id ?? null
    return {
      key: id ?? 'none',
      stage: id,
      title: stage ? t('tasks.form.grading.stageTitle', { n: index + 1, name: stage.name || '' }) : null,
      items: items.value
        .map((entry, at) => ({ entry, at }))
        .filter(({ entry }) => settledStage(props.stages, entry.stage) === id),
      lines: rubric.value
        .map((line, at) => ({ line, at }))
        .filter(({ line }) => settledStage(props.stages, line.stage) === id),
    }
  })
})

const staged = computed(() => props.stages.length > 0)

function setItem(index: number, patch: Partial<TaskSubmissionSchemaEntry>) {
  items.value = items.value.map((entry, i) => (i === index ? { ...entry, ...patch } : entry))
}
function addItem(stage: string | null) {
  items.value = [...items.value, { prompt: '', type: 'FILE', stage }]
}
function removeItem(index: number) {
  items.value = items.value.filter((_, i) => i !== index)
}

function setLine(index: number, patch: Partial<TaskRubricLine>) {
  rubric.value = rubric.value.map((line, i) => (i === index ? { ...line, ...patch } : line))
}
function addLine(stage: string | null) {
  rubric.value = [...rubric.value, { item: '', points: 0, criteria: '', stage }]
}
function removeLine(index: number) {
  rubric.value = rubric.value.filter((_, i) => i !== index)
}

const sum = (lines: TaskRubricLine[]) => lines.reduce((total, line) => total + (Number(line.points) || 0), 0)
const total = computed(() => sum(rubric.value))
const nameMissing = (line: TaskRubricLine) => props.missingName && !line.item.trim() && !!line.criteria.trim()
</script>

<template>
  <TaskFormSection :title="t('tasks.form.grading.title')">
    <BaseField data-agent-field="submission" :label="t('tasks.form.grading.items')">
      <div class="tfg__groups" data-testid="task-form-submission-items">
        <div v-for="group in groups" :key="group.key" class="tfg__items">
          <p v-if="group.title" class="tfg__stage t-eyebrow-read" data-user-content>{{ group.title }}</p>
          <div v-for="({ entry, at }, n) in group.items" :key="at" class="tfg__item">
            <span class="tfg__n t-num">{{ n + 1 }}</span>
            <v-text-field
              :model-value="entry.prompt"
              :aria-label="t('tasks.form.grading.itemName', { n: n + 1 })"
              :placeholder="t('tasks.form.grading.itemPlaceholder')"
              autocomplete="off"
              maxlength="255"
              variant="outlined"
              density="compact"
              hide-details
              class="tfg__grow"
              @update:model-value="setItem(at, { prompt: String($event ?? '') })"
            />
            <SegmentedControl
              :model-value="entry.type"
              :options="typeOptions"
              :label="t('tasks.form.grading.itemType', { n: n + 1 })"
              size="sm"
              @update:model-value="setItem(at, { type: $event as TaskSubmissionEntryType })"
            />
            <BaseButton
              kind="ghost"
              size="sm"
              icon="mdi-close"
              :disabled="group.items.length < 2"
              :title="t('tasks.form.grading.removeItem', { n: n + 1 })"
              :aria-label="t('tasks.form.grading.removeItem', { n: n + 1 })"
              @click="removeItem(at)"
            />
          </div>
          <BaseButton
            kind="secondary"
            size="sm"
            prepend-icon="mdi-plus"
            class="tfg__add"
            @click="addItem(group.stage)"
            >{{ t('tasks.form.grading.addItem') }}</BaseButton
          >
        </div>
      </div>
    </BaseField>

    <BaseField
      data-agent-field="rubric"
      :label="t('tasks.form.grading.rubric')"
      :error="missingName ? t('tasks.form.grading.nameRequired') : undefined"
    >
      <div class="tfg__groups" data-testid="task-form-rubric">
        <div v-for="group in groups" :key="group.key" class="tfg__rubric">
          <p v-if="group.title" class="tfg__stage t-eyebrow-read" data-user-content>{{ group.title }}</p>
          <p v-if="!group.lines.length" class="tfg__empty t-meta-read">{{ t('tasks.form.grading.rubricEmpty') }}</p>
          <template v-else>
            <div class="tfg__line tfg__head t-eyebrow-read" aria-hidden="true">
              <span>{{ t('tasks.form.grading.lineItem') }}</span>
              <span>{{ t('tasks.form.grading.linePoints') }}</span>
              <span>{{ t('tasks.form.grading.lineCriteria') }}</span>
              <span />
            </div>
            <div v-for="({ line, at }, n) in group.lines" :key="at" class="tfg__line">
              <v-text-field
                :model-value="line.item"
                :aria-label="t('tasks.form.grading.lineItemOf', { n: n + 1 })"
                :placeholder="t('tasks.form.grading.lineItemPlaceholder')"
                :error="nameMissing(line)"
                autocomplete="off"
                maxlength="64"
                variant="outlined"
                density="compact"
                hide-details
                @update:model-value="setLine(at, { item: String($event ?? '') })"
              />
              <v-text-field
                :model-value="line.points"
                :aria-label="t('tasks.form.grading.linePointsOf', { n: n + 1 })"
                type="number"
                min="0"
                max="1000"
                variant="outlined"
                density="compact"
                hide-details
                class="tfg__points"
                @update:model-value="setLine(at, { points: $event === '' ? 0 : Number($event) })"
              />
              <v-textarea
                :model-value="line.criteria"
                :aria-label="t('tasks.form.grading.lineCriteriaOf', { n: n + 1 })"
                autocomplete="off"
                rows="1"
                auto-grow
                maxlength="2000"
                variant="outlined"
                density="compact"
                hide-details
                @update:model-value="setLine(at, { criteria: String($event ?? '') })"
              />
              <BaseButton
                kind="ghost"
                size="sm"
                icon="mdi-close"
                :title="t('tasks.form.grading.removeLine', { n: n + 1 })"
                :aria-label="t('tasks.form.grading.removeLine', { n: n + 1 })"
                @click="removeLine(at)"
              />
            </div>
          </template>
          <div class="tfg__foot">
            <BaseButton kind="secondary" size="sm" prepend-icon="mdi-plus" @click="addLine(group.stage)">{{
              t('tasks.form.grading.addLine')
            }}</BaseButton>
            <span v-if="staged && group.lines.length" class="tfg__total t-meta-read">{{
              t('tasks.form.grading.stageTotal', { n: sum(group.lines.map(({ line }) => line)) })
            }}</span>
            <span v-else-if="!staged && rubric.length" class="tfg__total t-meta-read">{{
              t('tasks.form.grading.total', { n: total })
            }}</span>
          </div>
        </div>
        <span v-if="staged && rubric.length" class="tfg__total t-meta-read">{{
          t('tasks.form.grading.total', { n: total })
        }}</span>
      </div>
    </BaseField>

    <v-switch
      v-model="rubricPublic"
      data-agent-field="rubric_public"
      color="primary"
      density="compact"
      hide-details
      inset
      :label="t('tasks.form.grading.public')"
      data-testid="task-form-rubric-public"
    />
  </TaskFormSection>
</template>

<style scoped src="./task-form.css"></style>
<style scoped>
.tfg__groups {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.tfg__items,
.tfg__rubric {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.tfg__stage {
  margin: 0;
  color: var(--muted);
}

.tfg__item {
  display: flex;
  align-items: center;
  gap: 8px;
}

.tfg__n {
  flex: none;
  width: 16px;
  color: var(--muted);
  font-size: 13px;
  text-align: right;
}

.tfg__grow {
  flex: 1 1 auto;
  min-width: 0;
}

.tfg__add {
  align-self: flex-start;
}

.tfg__line {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 88px minmax(0, 2fr) 32px;
  gap: 8px;
  align-items: start;
}

.tfg__head {
  color: var(--muted);
}

.tfg__empty {
  margin: 0;
}

.tfg__foot {
  display: flex;
  align-items: center;
  gap: 8px;
}

.tfg__total {
  margin-left: auto;
}

@media (max-width: 599px) {
  .tfg__line {
    grid-template-columns: minmax(0, 1fr) 72px 32px;
  }

  .tfg__line > :nth-child(3) {
    grid-column: 1 / span 2;
    grid-row: 2;
  }

  .tfg__head {
    display: none;
  }
}
</style>
