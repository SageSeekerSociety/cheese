<script setup lang="ts">
// 评分要点里「测试点」那一行，连同它所在阶段挂的那一套测试点（方案 §出题人：配测试点）。
//
// 一行一套：这一行的分值就是测试占的分，测试点自己的分只决定内部的轻重，最后按比例折算到
// 这一行上（后端 `auto_score`）。顶上选形态 —— 输入/输出用例、测试脚本、命令判分 —— 下面是
// 三种共用的运行环境、代码目录、内存，再下面是这一种自己的设置（`TaskFormSuiteIo`、
// `TaskFormSuiteScript`、`TaskFormSuiteCommand`）。换形态时别的形态那几项清掉
// （`switchKind`），但这一次编辑里换回来，原来填的还在（记在这里，不送出去）。
// 「用参考答案试跑」打开试跑弹窗，跑的是这里现在填的样子，题目不必已经保存。
//
// 它只画：行和那一套都是 props 进来的，改一次往上报一次。
import type { TaskRubricLine, TaskTestRuntime, TaskTestSuite, TaskTestSuiteKind } from '@/types'

import { computed, inject, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import TaskFormSuiteCommand from './TaskFormSuiteCommand.vue'
import TaskFormSuiteIo from './TaskFormSuiteIo.vue'
import TaskFormSuiteScript from './TaskFormSuiteScript.vue'
import TaskTestTrialDialog from './TaskTestTrialDialog.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseField from '@/components/base/BaseField.vue'
import SegmentedControl from '@/components/common/SegmentedControl.vue'
import { runtimeChanged, switchKind } from '@/lib/taskTestSuite'
import { TEST_TRIAL } from '@/lib/testTrial'
import { TASK_TEST_KINDS, TASK_TEST_RUNTIMES } from '@/types'

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
const kindOptions = computed(() => TASK_TEST_KINDS.map((key) => ({ value: key, label: t(`tasks.judge.kind.${key}`) })))

function set(patch: Partial<TaskTestSuite>) {
  emit('update:suite', { ...props.suite, ...patch })
}

/** 这一次编辑里换走的那几种形态各自填成什么样：换回来时接着用。 */
const stash = new Map<TaskTestSuiteKind, TaskTestSuite>()

function setKind(kind: TaskTestSuiteKind) {
  const current = props.suite
  if (kind === current.kind) return
  stash.set(current.kind, current)
  const kept = stash.get(kind)
  if (!kept) {
    emit('update:suite', switchKind(current, kind))
    return
  }
  // 三种共用的几项按现在的来；运行环境变过，测试脚本的默认运行命令跟着换。
  const back = runtimeChanged(kept, current.runtime)
  emit('update:suite', {
    ...back,
    stage: current.stage,
    codeDir: current.codeDir,
    showHiddenNames: current.showHiddenNames,
  })
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

    <div class="tfts__kind">
      <SegmentedControl
        :model-value="suite.kind"
        :options="kindOptions"
        :label="t('tasks.judge.kind.label')"
        size="sm"
        data-testid="task-form-tests-kind"
        @update:model-value="setKind($event as TaskTestSuiteKind)"
      />
      <span class="t-meta-read">{{ t(`tasks.judge.kind.hint.${suite.kind}`) }}</span>
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
          @update:model-value="emit('update:suite', runtimeChanged(suite, $event as TaskTestRuntime))"
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
    </div>

    <TaskFormSuiteIo v-if="suite.kind === 'IO'" :suite="suite" :line-points="Number(line.points) || 0" @update="set">
      <template #actions>
        <BaseButton
          v-if="canTrial"
          kind="secondary"
          size="sm"
          prepend-icon="mdi-play-outline"
          data-testid="task-form-tests-trial"
          @click="trialOpen = true"
          >{{ t('tasks.judge.form.trial') }}</BaseButton
        >
      </template>
    </TaskFormSuiteIo>
    <template v-else>
      <TaskFormSuiteScript v-if="suite.kind === 'SCRIPT'" :suite="suite" @update="set" />
      <TaskFormSuiteCommand v-else :suite="suite" @update="set" />
      <div v-if="canTrial" class="tfts__foot">
        <BaseButton
          kind="secondary"
          size="sm"
          prepend-icon="mdi-play-outline"
          data-testid="task-form-tests-trial"
          @click="trialOpen = true"
          >{{ t('tasks.judge.form.trial') }}</BaseButton
        >
      </div>
    </template>

    <v-switch
      v-if="suite.kind !== 'COMMAND'"
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

.tfts__kind {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.tfts__grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
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

  .tfts__grid {
    grid-template-columns: 1fr;
  }
}
</style>
