<script setup lang="ts">
// 测试脚本那一种测试点（方案 §测试脚本）：出题人的测试文件评测前拷进学生代码旁边的
// `tests/`，运行命令把 JUnit XML 报告写到 `$JUDGE_REPORT`，按报告逐个测试给分。
//
// 两种给分：每个测试一样重（不配分，报告里每个测试 1 分），或者逐个测试配分（只按配了的
// 那几条算，报告里多出来的 0 分，配了却没跑到的算没过）。逐个配分时每一行要写测试名
// （`classname::name`，或只写 `name`），可以单独设成样例 —— 学生看得到样例的失败信息。
//
// 它只画：那一套是 props 进来的，改一次往上报一次改了的几项。
import type { TaskTestCase, TaskTestFile, TaskTestSuite, TaskTestVisibility } from '@/types'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import TaskFormSuiteFiles from './TaskFormSuiteFiles.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseField from '@/components/base/BaseField.vue'
import SegmentedControl from '@/components/common/SegmentedControl.vue'
import { MAX_STUDENT_LOG_LINES, MAX_TOTAL_TIME_S, MIN_TOTAL_TIME_S } from '@/lib/taskTestSuite'
import { MAX_CASES } from '@/lib/testCaseZip'

const props = defineProps<{ suite: TaskTestSuite }>()

const emit = defineEmits<{ update: [patch: Partial<TaskTestSuite>] }>()

const { t } = useI18n()

const set = (patch: Partial<TaskTestSuite>) => emit('update', patch)
const number = (value: unknown) => (value === '' || value === null ? 0 : Number(value))

/** 逐个配分：配了用例行就是。换回「一样重」清掉配分行。 */
const perTest = computed(() => props.suite.cases.length > 0)
const modeOptions = computed(() => [
  { value: 'AVERAGE', label: t('tasks.judge.script.average') },
  { value: 'PER_TEST', label: t('tasks.judge.script.perTest') },
])
const visibilityOptions = computed(() => [
  { value: 'SAMPLE' as const, label: t('tasks.judge.form.sample') },
  { value: 'HIDDEN' as const, label: t('tasks.judge.form.hidden') },
])

const blankRow = (): TaskTestCase => ({ testId: '', name: '', points: 10, visibility: 'HIDDEN' })

function setMode(mode: string) {
  if (mode === 'PER_TEST' && !perTest.value) set({ cases: [blankRow()] })
  if (mode === 'AVERAGE' && perTest.value) set({ cases: [] })
}

function setRow(index: number, patch: Partial<TaskTestCase>) {
  set({ cases: props.suite.cases.map((entry, i) => (i === index ? { ...entry, ...patch } : entry)) })
}
const addRow = () => set({ cases: [...props.suite.cases, blankRow()] })
const removeRow = (index: number) => set({ cases: props.suite.cases.filter((_, i) => i !== index) })

const rowPoints = computed(() => props.suite.cases.reduce((sum, entry) => sum + (Number(entry.points) || 0), 0))
</script>

<template>
  <div class="tfss">
    <p class="tfss__warn" data-testid="task-form-script-warning">{{ t('tasks.judge.script.limitation') }}</p>

    <TaskFormSuiteFiles
      :files="suite.testFiles ?? []"
      :label="t('tasks.judge.script.files')"
      :hint="t('tasks.judge.script.filesHint')"
      testid="task-form-script-files"
      @update:files="set({ testFiles: $event as TaskTestFile[] })"
    />

    <div class="tfss__grid">
      <BaseField
        class="tfss__wide"
        :label="t('tasks.judge.form.runCmd')"
        :hint="t('tasks.judge.script.runHint')"
        required
      >
        <v-text-field
          :model-value="suite.runCmd"
          autocomplete="off"
          maxlength="4000"
          variant="outlined"
          density="compact"
          hide-details
          class="tfss__mono"
          data-testid="task-form-script-run"
          @update:model-value="set({ runCmd: String($event ?? '') })"
        />
      </BaseField>
      <BaseField :label="t('tasks.judge.script.totalTime')" :hint="t('tasks.judge.script.totalTimeHint')">
        <v-text-field
          autocomplete="off"
          :model-value="suite.totalTimeLimitS"
          type="number"
          :min="MIN_TOTAL_TIME_S"
          :max="MAX_TOTAL_TIME_S"
          :suffix="t('tasks.judge.script.seconds')"
          variant="outlined"
          density="compact"
          hide-details
          data-testid="task-form-script-time"
          @update:model-value="set({ totalTimeLimitS: number($event) })"
        />
      </BaseField>
      <BaseField :label="t('tasks.judge.script.logLines')" :hint="t('tasks.judge.script.logLinesHint')">
        <v-text-field
          autocomplete="off"
          :model-value="suite.studentLogLines ?? 0"
          type="number"
          min="0"
          :max="MAX_STUDENT_LOG_LINES"
          :suffix="t('tasks.judge.script.lines')"
          variant="outlined"
          density="compact"
          hide-details
          @update:model-value="set({ studentLogLines: number($event) })"
        />
      </BaseField>
    </div>

    <div class="tfss__scoring" data-testid="task-form-script-scoring">
      <div class="tfss__head">
        <span class="tfss__label">{{ t('tasks.judge.script.scoring') }}</span>
        <SegmentedControl
          :model-value="perTest ? 'PER_TEST' : 'AVERAGE'"
          :options="modeOptions"
          :label="t('tasks.judge.script.scoring')"
          size="sm"
          data-testid="task-form-script-mode"
          @update:model-value="setMode(String($event))"
        />
      </div>
      <p class="tfss__hint t-meta-read">
        {{ perTest ? t('tasks.judge.script.perTestHint', { points: rowPoints }) : t('tasks.judge.script.averageHint') }}
      </p>
      <template v-if="perTest">
        <div v-for="(entry, index) in suite.cases" :key="index" class="tfss__row">
          <v-text-field
            :model-value="entry.testId ?? ''"
            :aria-label="t('tasks.judge.script.testId', { n: index + 1 })"
            :placeholder="t('tasks.judge.script.testIdPlaceholder')"
            autocomplete="off"
            maxlength="128"
            variant="outlined"
            density="compact"
            hide-details
            class="tfss__mono"
            :data-testid="`task-form-script-test-${index}`"
            @update:model-value="setRow(index, { testId: String($event ?? '') })"
          />
          <v-text-field
            :model-value="entry.name"
            :aria-label="t('tasks.judge.script.name', { n: index + 1 })"
            :placeholder="t('tasks.judge.script.namePlaceholder')"
            autocomplete="off"
            maxlength="64"
            variant="outlined"
            density="compact"
            hide-details
            @update:model-value="setRow(index, { name: String($event ?? '') })"
          />
          <SegmentedControl
            :model-value="entry.visibility"
            :options="visibilityOptions"
            :label="t('tasks.judge.form.caseVisibility', { n: index + 1 })"
            size="sm"
            @update:model-value="setRow(index, { visibility: $event as TaskTestVisibility })"
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
            :data-testid="`task-form-script-points-${index}`"
            @update:model-value="setRow(index, { points: number($event) })"
          />
          <BaseButton
            kind="ghost"
            size="sm"
            icon="mdi-close"
            :title="t('tasks.judge.form.removeCase', { n: index + 1 })"
            :aria-label="t('tasks.judge.form.removeCase', { n: index + 1 })"
            @click="removeRow(index)"
          />
        </div>
        <div class="tfss__foot">
          <BaseButton
            kind="secondary"
            size="sm"
            prepend-icon="mdi-plus"
            :disabled="suite.cases.length >= MAX_CASES"
            data-testid="task-form-script-add-test"
            @click="addRow"
            >{{ t('tasks.judge.script.addTest') }}</BaseButton
          >
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.tfss {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.tfss__warn {
  margin: 0;
  padding: 8px 12px;
  border-radius: var(--radius-sm);
  background: var(--warn-wash);
  color: var(--warn-ink);
  font-size: 12px;
}

.tfss__grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.tfss__wide {
  grid-column: 1 / -1;
}

.tfss__mono :deep(input) {
  font-family: var(--font-mono);
  font-size: 13px;
}

.tfss__scoring {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.tfss__head {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.tfss__label {
  color: var(--ink);
  font-size: 13px;
  font-weight: 500;
}

.tfss__hint {
  margin: 0;
}

.tfss__row {
  display: grid;
  grid-template-columns: minmax(0, 3fr) minmax(0, 2fr) auto 72px 32px;
  gap: 8px;
  align-items: center;
}

.tfss__foot {
  display: flex;
  gap: 8px;
}

@media (max-width: 599px) {
  .tfss__grid {
    grid-template-columns: 1fr;
  }

  .tfss__row {
    grid-template-columns: minmax(0, 1fr) 72px 32px;
  }

  .tfss__row > :nth-child(2),
  .tfss__row > :nth-child(3) {
    grid-column: 1 / span 3;
  }
}
</style>
