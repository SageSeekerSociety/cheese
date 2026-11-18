<script setup lang="ts">
// 评分要点里「测试点」那一行，连同它所在阶段挂的那一套输入/输出用例（方案 §出题人：
// 配测试点）。
//
// 一行一套：这一行的分值就是测试占的分，用例分只决定用例之间的轻重，最后按比例折算到
// 这一行上（后端 `auto_score`）。运行设置在上，用例列表在下；一个用例点开才显示输入和
// 期望输出，免得十几个用例把表单撑得很长。「用参考答案试跑」打开试跑弹窗，跑的是这里
// 现在填的样子，题目不必已经保存。
//
// 它只画：行和那一套都是 props 进来的，改一次往上报一次。
import type {
  TaskRubricLine,
  TaskTestCase,
  TaskTestCompare,
  TaskTestRuntime,
  TaskTestSuite,
  TaskTestVisibility,
} from '@/types'

import { computed, inject, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import TaskTestTrialDialog from './TaskTestTrialDialog.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseField from '@/components/base/BaseField.vue'
import SegmentedControl from '@/components/common/SegmentedControl.vue'
import { TEST_TRIAL } from '@/lib/testTrial'
import { TASK_TEST_COMPARES, TASK_TEST_RUNTIMES } from '@/types'

const props = defineProps<{
  line: TaskRubricLine
  suite: TaskTestSuite
}>()

const emit = defineEmits<{
  'update:line': [patch: Partial<TaskRubricLine>]
  'update:suite': [suite: TaskTestSuite]
  remove: []
}>()

const { t } = useI18n()

const runtimeItems = computed(() =>
  TASK_TEST_RUNTIMES.map((key) => ({ value: key, title: t(`tasks.judge.runtime.${key.replace('.', '_')}`) }))
)
const compareItems = computed(() =>
  TASK_TEST_COMPARES.map((key) => ({ value: key, title: t(`tasks.judge.compare.${key}`) }))
)
const visibilityOptions = computed(() => [
  { value: 'SAMPLE' as const, label: t('tasks.judge.form.sample') },
  { value: 'HIDDEN' as const, label: t('tasks.judge.form.hidden') },
])

function set(patch: Partial<TaskTestSuite>) {
  emit('update:suite', { ...props.suite, ...patch })
}

function setCase(index: number, patch: Partial<TaskTestCase>) {
  set({ cases: props.suite.cases.map((entry, i) => (i === index ? { ...entry, ...patch } : entry)) })
}

/** 点开了哪几个用例（看输入、期望输出）。新加的那一个直接点开。 */
const open = ref<Set<number>>(new Set())
function toggle(index: number) {
  const next = new Set(open.value)
  if (next.has(index)) next.delete(index)
  else next.add(index)
  open.value = next
}

function addCase() {
  const cases = props.suite.cases
  set({
    cases: [
      ...cases,
      { name: '', points: 10, visibility: cases.length ? 'HIDDEN' : 'SAMPLE', input: '', expected: '' },
    ],
  })
  open.value = new Set([...open.value, cases.length])
}

function removeCase(index: number) {
  set({ cases: props.suite.cases.filter((_, i) => i !== index) })
  open.value = new Set([...open.value].filter((i) => i !== index).map((i) => (i > index ? i - 1 : i)))
}

const casePoints = computed(() => props.suite.cases.reduce((sum, entry) => sum + (Number(entry.points) || 0), 0))
const sampleCount = computed(() => props.suite.cases.filter((entry) => entry.visibility === 'SAMPLE').length)

/** 一个用例在这一行上折成几分（四舍五入，只是给出题人看个大概）。 */
function shareOf(entry: TaskTestCase): number {
  if (!casePoints.value) return 0
  return Math.round(((Number(entry.points) || 0) / casePoints.value) * (Number(props.line.points) || 0))
}

const number = (value: unknown) => (value === '' || value === null ? 0 : Number(value))

const trialOpen = ref(false)
/** 页面放了试跑才显示「试跑」（组件预览站、一次发好几道时没有）。 */
const canTrial = inject(TEST_TRIAL, null) !== null
</script>

<template>
  <div class="tfts" data-testid="task-form-tests-line">
    <div class="tfts__line">
      <v-text-field
        :model-value="line.item"
        :aria-label="t('tasks.judge.form.lineName')"
        :placeholder="t('tasks.judge.form.lineNamePlaceholder')"
        autocomplete="off"
        maxlength="64"
        variant="outlined"
        density="compact"
        hide-details
        data-testid="task-form-tests-name"
        @update:model-value="emit('update:line', { item: String($event ?? '') })"
      />
      <v-text-field
        autocomplete="off"
        :model-value="line.points"
        :aria-label="t('tasks.judge.form.linePoints')"
        type="number"
        min="0"
        max="1000"
        variant="outlined"
        density="compact"
        hide-details
        class="tfts__points"
        data-testid="task-form-tests-points"
        @update:model-value="emit('update:line', { points: number($event) })"
      />
      <span class="tfts__rule">
        <span class="tfts__tag t-meta-read">{{ t('tasks.judge.form.auto') }}</span>
        {{ t('tasks.judge.form.rule') }}
      </span>
      <BaseButton
        kind="ghost"
        size="sm"
        icon="mdi-close"
        :title="t('tasks.judge.form.remove')"
        :aria-label="t('tasks.judge.form.remove')"
        @click="emit('remove')"
      />
    </div>

    <div class="tfts__grid">
      <BaseField :label="t('tasks.judge.form.runtime')">
        <v-select
          autocomplete="off"
          :model-value="suite.runtime"
          :items="runtimeItems"
          item-title="title"
          item-value="value"
          variant="outlined"
          density="compact"
          hide-details
          data-testid="task-form-tests-runtime"
          @update:model-value="set({ runtime: $event as TaskTestRuntime })"
        />
      </BaseField>
      <BaseField :label="t('tasks.judge.form.codeDir')" :hint="t('tasks.judge.form.codeDirHint')">
        <v-text-field
          :model-value="suite.codeDir"
          :placeholder="t('tasks.judge.form.codeDirPlaceholder')"
          autocomplete="off"
          maxlength="255"
          variant="outlined"
          density="compact"
          hide-details
          @update:model-value="set({ codeDir: String($event ?? '') })"
        />
      </BaseField>
      <BaseField :label="t('tasks.judge.form.compileCmd')" :hint="t('tasks.judge.form.compileCmdHint')">
        <v-text-field
          :model-value="suite.compileCmd"
          autocomplete="off"
          maxlength="4000"
          variant="outlined"
          density="compact"
          hide-details
          class="tfts__mono"
          @update:model-value="set({ compileCmd: String($event ?? '') })"
        />
      </BaseField>
      <BaseField :label="t('tasks.judge.form.runCmd')" required>
        <v-text-field
          :model-value="suite.runCmd"
          autocomplete="off"
          maxlength="4000"
          variant="outlined"
          density="compact"
          hide-details
          class="tfts__mono"
          data-testid="task-form-tests-run"
          @update:model-value="set({ runCmd: String($event ?? '') })"
        />
      </BaseField>
      <BaseField :label="t('tasks.judge.form.timeLimit')" :hint="t('tasks.judge.form.timeLimitHint')">
        <v-text-field
          autocomplete="off"
          :model-value="suite.timeLimitMs"
          type="number"
          min="500"
          max="10000"
          step="100"
          suffix="ms"
          variant="outlined"
          density="compact"
          hide-details
          @update:model-value="set({ timeLimitMs: number($event) })"
        />
      </BaseField>
      <BaseField :label="t('tasks.judge.form.memory')">
        <v-text-field
          autocomplete="off"
          :model-value="suite.memoryMb"
          type="number"
          min="64"
          max="1024"
          suffix="MB"
          variant="outlined"
          density="compact"
          hide-details
          @update:model-value="set({ memoryMb: number($event) })"
        />
      </BaseField>
      <BaseField :label="t('tasks.judge.form.compare')">
        <v-select
          autocomplete="off"
          :model-value="suite.compare"
          :items="compareItems"
          item-title="title"
          item-value="value"
          variant="outlined"
          density="compact"
          hide-details
          @update:model-value="set({ compare: $event as TaskTestCompare })"
        />
      </BaseField>
      <BaseField v-if="suite.compare === 'NUMERIC'" :label="t('tasks.judge.form.tolerance')">
        <v-text-field
          autocomplete="off"
          :model-value="suite.tolerance"
          type="number"
          min="0"
          max="1"
          step="0.0001"
          variant="outlined"
          density="compact"
          hide-details
          @update:model-value="set({ tolerance: number($event) })"
        />
      </BaseField>
    </div>

    <div class="tfts__cases" data-testid="task-form-tests-cases">
      <div class="tfts__cases-head">
        <span class="tfts__label">{{ t('tasks.judge.form.cases', { n: suite.cases.length }) }}</span>
        <span class="t-meta-read">{{
          t('tasks.judge.form.casesSummary', { samples: sampleCount, points: casePoints })
        }}</span>
      </div>
      <div v-for="(entry, index) in suite.cases" :key="index" class="tfts__case">
        <div class="tfts__case-row">
          <BaseButton
            kind="ghost"
            size="sm"
            :icon="open.has(index) ? 'mdi-chevron-down' : 'mdi-chevron-right'"
            :aria-expanded="open.has(index)"
            :aria-label="t('tasks.judge.form.toggleCase', { n: index + 1 })"
            @click="toggle(index)"
          />
          <v-text-field
            :model-value="entry.name"
            :aria-label="t('tasks.judge.form.caseName', { n: index + 1 })"
            :placeholder="t('tasks.judge.form.caseNamePlaceholder', { n: index + 1 })"
            autocomplete="off"
            maxlength="64"
            variant="outlined"
            density="compact"
            hide-details
            @update:model-value="setCase(index, { name: String($event ?? '') })"
          />
          <SegmentedControl
            :model-value="entry.visibility"
            :options="visibilityOptions"
            :label="t('tasks.judge.form.caseVisibility', { n: index + 1 })"
            size="sm"
            @update:model-value="setCase(index, { visibility: $event as TaskTestVisibility })"
          />
          <v-text-field
            autocomplete="off"
            :model-value="entry.points"
            :aria-label="t('tasks.judge.form.casePoints', { n: index + 1 })"
            type="number"
            min="0"
            max="1000"
            variant="outlined"
            density="compact"
            hide-details
            class="tfts__case-points"
            @update:model-value="setCase(index, { points: number($event) })"
          />
          <span class="tfts__share t-meta-read t-num">{{ t('tasks.judge.form.share', { n: shareOf(entry) }) }}</span>
          <BaseButton
            kind="ghost"
            size="sm"
            icon="mdi-close"
            :disabled="suite.cases.length < 2"
            :title="t('tasks.judge.form.removeCase', { n: index + 1 })"
            :aria-label="t('tasks.judge.form.removeCase', { n: index + 1 })"
            @click="removeCase(index)"
          />
        </div>
        <div v-if="open.has(index)" class="tfts__io">
          <BaseField :label="t('tasks.judge.form.input')">
            <v-textarea
              autocomplete="off"
              :model-value="entry.input ?? ''"
              rows="3"
              auto-grow
              variant="outlined"
              density="compact"
              hide-details
              class="tfts__mono"
              :data-testid="`task-form-tests-input-${index}`"
              @update:model-value="setCase(index, { input: String($event ?? '') })"
            />
          </BaseField>
          <BaseField :label="t('tasks.judge.form.expected')">
            <v-textarea
              autocomplete="off"
              :model-value="entry.expected ?? ''"
              rows="3"
              auto-grow
              variant="outlined"
              density="compact"
              hide-details
              class="tfts__mono"
              :data-testid="`task-form-tests-expected-${index}`"
              @update:model-value="setCase(index, { expected: String($event ?? '') })"
            />
          </BaseField>
        </div>
      </div>
      <div class="tfts__foot">
        <BaseButton
          kind="secondary"
          size="sm"
          prepend-icon="mdi-plus"
          data-testid="task-form-tests-add-case"
          @click="addCase"
          >{{ t('tasks.judge.form.addCase') }}</BaseButton
        >
        <BaseButton
          v-if="canTrial"
          kind="secondary"
          size="sm"
          prepend-icon="mdi-play-outline"
          data-testid="task-form-tests-trial"
          @click="trialOpen = true"
          >{{ t('tasks.judge.form.trial') }}</BaseButton
        >
      </div>
    </div>

    <v-switch
      :model-value="suite.showHiddenNames"
      color="primary"
      density="compact"
      hide-details
      inset
      :label="t('tasks.judge.form.showHiddenNames')"
      @update:model-value="set({ showHiddenNames: !!$event })"
    />

    <TaskTestTrialDialog v-if="canTrial" v-model="trialOpen" :suite="suite" />
  </div>
</template>

<style scoped>
.tfts {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 12px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
}

.tfts__line {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 88px minmax(0, 2fr) 32px;
  gap: 8px;
  align-items: start;
}

.tfts__points {
  max-width: 88px;
}

.tfts__rule {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-top: 6px;
  color: var(--muted);
  font-size: 12px;
}

.tfts__tag {
  flex: none;
  padding: 1px 8px;
  border-radius: var(--radius-sm);
  background: var(--fill-2);
  color: var(--ink);
}

.tfts__grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.tfts__label {
  color: var(--ink);
  font-size: 13px;
  font-weight: 500;
}

.tfts__cases {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.tfts__cases-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.tfts__case {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.tfts__case-row {
  display: grid;
  grid-template-columns: 32px minmax(0, 1fr) auto 72px 56px 32px;
  gap: 8px;
  align-items: center;
}

.tfts__share {
  color: var(--muted);
  text-align: right;
}

.tfts__io {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  padding-left: 40px;
}

.tfts__mono :deep(input),
.tfts__mono :deep(textarea) {
  font-family: var(--font-mono);
  font-size: 13px;
}

.tfts__foot {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

@media (max-width: 599px) {
  .tfts__line {
    grid-template-columns: minmax(0, 1fr) 72px 32px;
  }

  .tfts__line > .tfts__rule {
    grid-column: 1 / span 3;
    grid-row: 2;
  }

  .tfts__grid,
  .tfts__io {
    grid-template-columns: 1fr;
    padding-left: 0;
  }

  .tfts__case-row {
    grid-template-columns: 32px minmax(0, 1fr) 72px 32px;
  }

  .tfts__case-row > :nth-child(3) {
    grid-column: 2 / span 3;
    grid-row: 2;
  }

  .tfts__share {
    display: none;
  }
}
</style>
