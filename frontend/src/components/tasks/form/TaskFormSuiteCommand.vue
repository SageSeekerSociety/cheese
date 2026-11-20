<script setup lang="ts">
// 命令判分那一种测试点（方案 §命令判分）：按顺序跑几条命令（第一条失败就停），分从最后
// 一行 `SCORE: x/y` 或仓库根的 score.json 读。
//
// 评测前平台按原路径放回两样东西：保护文件（起始代码里出题人点名的那几份，学生改了也会被
// 换回原版 —— 所以出题人要把原文传上来）和判分文件（只有出题人有的判分程序、数据）。学生
// 提交里的 score.json 先删掉。
//
// 它只画：那一套是 props 进来的，改一次往上报一次改了的几项。
import type { TaskTestFile, TaskTestScoreSource, TaskTestSuite } from '@/types'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import TaskFormSuiteFiles from './TaskFormSuiteFiles.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseField from '@/components/base/BaseField.vue'
import SegmentedControl from '@/components/common/SegmentedControl.vue'
import { MAX_COMMANDS, MAX_STUDENT_LOG_LINES, MAX_TOTAL_TIME_S, MIN_TOTAL_TIME_S } from '@/lib/taskTestSuite'

const props = defineProps<{ suite: TaskTestSuite }>()

const emit = defineEmits<{ update: [patch: Partial<TaskTestSuite>] }>()

const { t } = useI18n()

const set = (patch: Partial<TaskTestSuite>) => emit('update', patch)
const number = (value: unknown) => (value === '' || value === null ? 0 : Number(value))

const commands = computed(() => (props.suite.commands?.length ? props.suite.commands : ['']))

function setCommand(index: number, text: string) {
  set({ commands: commands.value.map((cmd, i) => (i === index ? text : cmd)) })
}
const addCommand = () => set({ commands: [...commands.value, ''] })
const removeCommand = (index: number) => set({ commands: commands.value.filter((_, i) => i !== index) })
function moveCommand(index: number, by: -1 | 1) {
  const next = [...commands.value]
  const to = index + by
  if (to < 0 || to >= next.length) return
  ;[next[index], next[to]] = [next[to]!, next[index]!]
  set({ commands: next })
}

const sourceOptions = computed(() => [
  { value: 'LINE' as const, label: t('tasks.judge.command.sourceLine') },
  { value: 'JSON' as const, label: t('tasks.judge.command.sourceJson') },
])
</script>

<template>
  <div class="tfsc">
    <p class="tfsc__warn">{{ t('tasks.judge.command.limitation') }}</p>

    <div class="tfsc__commands" data-testid="task-form-command-list">
      <span class="tfsc__label">{{ t('tasks.judge.command.commands') }}</span>
      <p class="tfsc__hint t-meta-read">{{ t('tasks.judge.command.commandsHint') }}</p>
      <div v-for="(cmd, index) in commands" :key="index" class="tfsc__cmd">
        <span class="tfsc__n t-num t-meta-read" aria-hidden="true">{{ index + 1 }}</span>
        <v-text-field
          :model-value="cmd"
          :aria-label="t('tasks.judge.command.command', { n: index + 1 })"
          :placeholder="index === 0 ? t('tasks.judge.command.commandPlaceholder') : ''"
          autocomplete="off"
          maxlength="4000"
          variant="outlined"
          density="compact"
          hide-details
          class="tfsc__mono"
          :data-testid="`task-form-command-${index}`"
          @update:model-value="setCommand(index, String($event ?? ''))"
        />
        <BaseButton
          kind="ghost"
          size="sm"
          icon="mdi-arrow-up"
          :disabled="index === 0"
          :aria-label="t('tasks.judge.command.up', { n: index + 1 })"
          :data-testid="`task-form-command-up-${index}`"
          @click="moveCommand(index, -1)"
        />
        <BaseButton
          kind="ghost"
          size="sm"
          icon="mdi-arrow-down"
          :disabled="index === commands.length - 1"
          :aria-label="t('tasks.judge.command.down', { n: index + 1 })"
          @click="moveCommand(index, 1)"
        />
        <BaseButton
          kind="ghost"
          size="sm"
          icon="mdi-close"
          :disabled="commands.length < 2"
          :aria-label="t('tasks.judge.command.remove', { n: index + 1 })"
          :data-testid="`task-form-command-remove-${index}`"
          @click="removeCommand(index)"
        />
      </div>
      <div>
        <BaseButton
          kind="secondary"
          size="sm"
          prepend-icon="mdi-plus"
          :disabled="commands.length >= MAX_COMMANDS"
          data-testid="task-form-command-add"
          @click="addCommand"
          >{{ t('tasks.judge.command.add') }}</BaseButton
        >
      </div>
    </div>

    <div class="tfsc__source">
      <span class="tfsc__label">{{ t('tasks.judge.command.source') }}</span>
      <SegmentedControl
        :model-value="suite.scoreSource ?? 'LINE'"
        :options="sourceOptions"
        :label="t('tasks.judge.command.source')"
        size="sm"
        data-testid="task-form-command-source"
        @update:model-value="set({ scoreSource: $event as TaskTestScoreSource })"
      />
      <p class="tfsc__hint t-meta-read">
        {{
          (suite.scoreSource ?? 'LINE') === 'LINE'
            ? t('tasks.judge.command.sourceLineHint')
            : t('tasks.judge.command.sourceJsonHint')
        }}
      </p>
    </div>

    <TaskFormSuiteFiles
      :files="suite.protectedFiles ?? []"
      :label="t('tasks.judge.command.protected')"
      :hint="t('tasks.judge.command.protectedHint')"
      per-row
      testid="task-form-command-protected"
      @update:files="set({ protectedFiles: $event as TaskTestFile[] })"
    />

    <TaskFormSuiteFiles
      :files="suite.testFiles ?? []"
      :label="t('tasks.judge.command.graders')"
      :hint="t('tasks.judge.command.gradersHint')"
      testid="task-form-command-graders"
      @update:files="set({ testFiles: $event as TaskTestFile[] })"
    />

    <div class="tfsc__grid">
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
          data-testid="task-form-command-time"
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
          data-testid="task-form-command-log-lines"
          @update:model-value="set({ studentLogLines: number($event) })"
        />
      </BaseField>
    </div>
  </div>
</template>

<style scoped>
.tfsc {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.tfsc__warn {
  margin: 0;
  padding: 8px 12px;
  border-radius: var(--radius-sm);
  background: var(--warn-wash);
  color: var(--warn-ink);
  font-size: 12px;
}

.tfsc__label {
  color: var(--ink);
  font-size: 13px;
  font-weight: 500;
}

.tfsc__hint {
  margin: 0;
}

.tfsc__commands,
.tfsc__source {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.tfsc__source {
  align-items: flex-start;
}

.tfsc__cmd {
  display: grid;
  grid-template-columns: 20px minmax(0, 1fr) 32px 32px 32px;
  gap: 8px;
  align-items: center;
}

.tfsc__n {
  text-align: right;
}

.tfsc__mono :deep(input) {
  font-family: var(--font-mono);
  font-size: 13px;
}

.tfsc__grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

@media (max-width: 599px) {
  .tfsc__grid {
    grid-template-columns: 1fr;
  }
}
</style>
