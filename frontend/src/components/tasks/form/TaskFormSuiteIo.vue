<script setup lang="ts">
// 输入/输出用例那一种测试点的设置和用例（方案 §输入/输出用例）：编译与运行命令、每个用例
// 的时限、输出怎么比、学生每天能自测几次，下面是用例列表。一个用例点开才显示输入和期望
// 输出，免得十几个用例把表单撑得很长。
//
// 「导入用例」收一个装着成对文件的 zip（`1.in`/`1.out` 之类），在浏览器里拆开追加成隐藏
// 用例（`lib/testCaseZip.ts`）；超过 64 KB 的、超过 200 个的整包不导入，说是哪一份。
//
// 它只画：那一套是 props 进来的，改一次往上报一次改了的几项。
import type { TaskTestCase, TaskTestCompare, TaskTestSuite, TaskTestVisibility } from '@/types'

import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseField from '@/components/base/BaseField.vue'
import SegmentedControl from '@/components/common/SegmentedControl.vue'
import { casesFromZip, MAX_CASES } from '@/lib/testCaseZip'
import { TASK_TEST_COMPARES } from '@/types'

const props = defineProps<{
  suite: TaskTestSuite
  /** 测试点那一行的分值：每个用例折成几分照它算。 */
  linePoints: number
}>()

const emit = defineEmits<{ update: [patch: Partial<TaskTestSuite>] }>()

const { t } = useI18n()

const compareItems = computed(() =>
  TASK_TEST_COMPARES.map((key) => ({ value: key, title: t(`tasks.judge.compare.${key}`) }))
)
const visibilityOptions = computed(() => [
  { value: 'SAMPLE' as const, label: t('tasks.judge.form.sample') },
  { value: 'HIDDEN' as const, label: t('tasks.judge.form.hidden') },
])

const set = (patch: Partial<TaskTestSuite>) => emit('update', patch)

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
  return Math.round(((Number(entry.points) || 0) / casePoints.value) * (Number(props.linePoints) || 0))
}

const number = (value: unknown) => (value === '' || value === null ? 0 : Number(value))

/** 自测次数：空着就是不开。 */
function setSelfTest(value: unknown) {
  const n = value === '' || value == null ? null : Math.round(Number(value))
  set({ selfTestDaily: n && n > 0 ? n : null })
}

const zipInput = ref<HTMLInputElement | null>(null)
const importing = ref(false)
/** 上一次导入的结果：成功说导了几个，失败说为什么。 */
const importNote = ref<{ ok: boolean; text: string } | null>(null)

/** 表单上只有起步的那一个空用例时，导入的用例换掉它，不留一个空着的样例。 */
const onlyBlank = () =>
  props.suite.cases.length === 1 &&
  !props.suite.cases[0]!.name.trim() &&
  !(props.suite.cases[0]!.input ?? '').trim() &&
  !(props.suite.cases[0]!.expected ?? '').trim()

async function onZip(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  importing.value = true
  try {
    const keep = onlyBlank() ? [] : props.suite.cases
    const result = await casesFromZip(await file.arrayBuffer(), keep.length)
    if (!result.ok) {
      importNote.value = { ok: false, text: t(`tasks.judge.import.${result.error.key}`, result.error.params ?? {}) }
      return
    }
    set({ cases: [...keep, ...result.cases] })
    importNote.value = {
      ok: true,
      text: result.unpaired
        ? t('tasks.judge.import.doneUnpaired', { n: result.cases.length, skipped: result.unpaired })
        : t('tasks.judge.import.done', { n: result.cases.length }),
    }
  } finally {
    importing.value = false
  }
}
</script>

<template>
  <div class="tfsi">
    <div class="tfsi__grid">
      <BaseField :label="t('tasks.judge.form.compileCmd')" :hint="t('tasks.judge.form.compileCmdHint')">
        <v-text-field
          :model-value="suite.compileCmd"
          autocomplete="off"
          maxlength="4000"
          variant="outlined"
          density="compact"
          hide-details
          class="tfsi__mono"
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
          class="tfsi__mono"
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
      <BaseField :label="t('tasks.judge.form.selfTest')" :hint="t('tasks.judge.form.selfTestHint')">
        <v-text-field
          autocomplete="off"
          :model-value="suite.selfTestDaily ?? ''"
          type="number"
          min="1"
          max="500"
          :placeholder="t('tasks.judge.form.selfTestOff')"
          :suffix="t('tasks.judge.form.selfTestUnit')"
          variant="outlined"
          density="compact"
          hide-details
          data-testid="task-form-tests-self-test"
          @update:model-value="setSelfTest($event)"
        />
      </BaseField>
    </div>

    <div class="tfsi__cases" data-testid="task-form-tests-cases">
      <div class="tfsi__cases-head">
        <span class="tfsi__label">{{ t('tasks.judge.form.cases', { n: suite.cases.length }) }}</span>
        <span class="t-meta-read">{{
          t('tasks.judge.form.casesSummary', { samples: sampleCount, points: casePoints })
        }}</span>
      </div>
      <div v-for="(entry, index) in suite.cases" :key="index" class="tfsi__case">
        <div class="tfsi__case-row">
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
            class="tfsi__case-points"
            @update:model-value="setCase(index, { points: number($event) })"
          />
          <span class="tfsi__share t-meta-read t-num">{{ t('tasks.judge.form.share', { n: shareOf(entry) }) }}</span>
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
        <div v-if="open.has(index)" class="tfsi__io">
          <BaseField :label="t('tasks.judge.form.input')">
            <v-textarea
              autocomplete="off"
              :model-value="entry.input ?? ''"
              rows="3"
              auto-grow
              variant="outlined"
              density="compact"
              hide-details
              class="tfsi__mono"
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
              class="tfsi__mono"
              :data-testid="`task-form-tests-expected-${index}`"
              @update:model-value="setCase(index, { expected: String($event ?? '') })"
            />
          </BaseField>
        </div>
      </div>
      <p
        v-if="importNote"
        class="tfsi__note"
        :class="{ 'tfsi__note--bad': !importNote.ok }"
        :role="importNote.ok ? 'status' : 'alert'"
        data-testid="task-form-tests-import-note"
      >
        {{ importNote.text }}
      </p>
      <div class="tfsi__foot">
        <BaseButton
          kind="secondary"
          size="sm"
          prepend-icon="mdi-plus"
          :disabled="suite.cases.length >= MAX_CASES"
          data-testid="task-form-tests-add-case"
          @click="addCase"
          >{{ t('tasks.judge.form.addCase') }}</BaseButton
        >
        <BaseButton
          kind="secondary"
          size="sm"
          prepend-icon="mdi-folder-zip-outline"
          :loading="importing"
          :title="t('tasks.judge.import.hint')"
          data-testid="task-form-tests-import"
          @click="zipInput?.click()"
          >{{ t('tasks.judge.import.button') }}</BaseButton
        >
        <slot name="actions" />
      </div>
      <input
        ref="zipInput"
        type="file"
        accept=".zip,application/zip"
        hidden
        :aria-label="t('tasks.judge.import.button')"
        data-testid="task-form-tests-import-input"
        @change="onZip"
      />
    </div>
  </div>
</template>

<style scoped>
.tfsi {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.tfsi__grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.tfsi__label {
  color: var(--ink);
  font-size: 13px;
  font-weight: 500;
}

.tfsi__cases {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.tfsi__cases-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.tfsi__case {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.tfsi__case-row {
  display: grid;
  grid-template-columns: 32px minmax(0, 1fr) auto 72px 56px 32px;
  gap: 8px;
  align-items: center;
}

.tfsi__share {
  color: var(--muted);
  text-align: right;
}

.tfsi__io {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  padding-left: 40px;
}

.tfsi__mono :deep(input),
.tfsi__mono :deep(textarea) {
  font-family: var(--font-mono);
  font-size: 13px;
}

.tfsi__note {
  margin: 0;
  color: var(--muted);
  font-size: 12px;
}

.tfsi__note--bad {
  color: var(--danger-ink);
}

.tfsi__foot {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

@media (max-width: 599px) {
  .tfsi__grid,
  .tfsi__io {
    grid-template-columns: 1fr;
    padding-left: 0;
  }

  .tfsi__case-row {
    grid-template-columns: 32px minmax(0, 1fr) 72px 32px;
  }

  .tfsi__case-row > :nth-child(3) {
    grid-column: 2 / span 3;
    grid-row: 2;
  }

  .tfsi__share {
    display: none;
  }
}
</style>
