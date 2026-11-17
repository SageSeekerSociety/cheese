<script setup lang="ts">
// 用参考答案试跑一套测试点（方案 §出题人：配测试点）：贴几份参考代码，按表单里现在
// 填的测试点跑一遍，看每个用例过没过、最慢的用例离时限还有多少。
//
// 跑的是表单现在的样子，题目不必已经保存；试跑和学生的评测走同一条队列、同一台评测主机，
// 算力同样记在空间所有者头上。评测主机没接入时，后端很快以「评测暂不可用」标失败。
import type { TaskTestRuntime, TaskTestSuite, TaskTestTrialFile } from '@/types'

import { computed, inject, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import TaskJudgeResult from '../TaskJudgeResult.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseField from '@/components/base/BaseField.vue'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import { TEST_TRIAL } from '@/lib/testTrial'

const open = defineModel<boolean>({ default: false })

const props = defineProps<{ suite: TaskTestSuite }>()

const { t } = useI18n()

/** 每种运行环境的参考代码默认叫什么（运行命令照这个名字写最省事）。 */
const DEFAULT_FILE: Record<TaskTestRuntime, string> = {
  'python3.12': 'main.py',
  cpp17: 'main.cpp',
  java21: 'Main.java',
  node22: 'main.js',
  'go1.23': 'main.go',
}

const files = ref<TaskTestTrialFile[]>([])
watch(
  open,
  (shown) => {
    if (shown && !files.value.length) files.value = [{ path: DEFAULT_FILE[props.suite.runtime] ?? 'main', content: '' }]
  },
  { immediate: true }
)

function setFile(index: number, patch: Partial<TaskTestTrialFile>) {
  files.value = files.value.map((file, i) => (i === index ? { ...file, ...patch } : file))
}
function addFile() {
  files.value = [...files.value, { path: '', content: '' }]
}
function removeFile(index: number) {
  files.value = files.value.filter((_, i) => i !== index)
}

const runner = inject(TEST_TRIAL, null)
const trial = computed(() => runner?.trial.value ?? null)
const starting = computed(() => runner?.starting.value ?? false)
const error = computed(() => runner?.error.value ?? '')
// 一页上可能有几套测试点（分阶段的题），共用一个试跑：打开时清掉上一套的结果。
watch(open, (shown) => {
  if (shown) runner?.clear()
  else runner?.stop()
})

const inFlight = (state: string | undefined) => state === 'queued' || state === 'running'
const ready = computed(
  () => files.value.some((file) => file.path.trim() && file.content.trim()) && !!props.suite.runCmd.trim()
)
const busy = computed(() => starting.value || inFlight(trial.value?.state))

function start() {
  if (!runner || !ready.value || busy.value) return
  void runner.run(
    props.suite,
    files.value.filter((file) => file.path.trim()).map((file) => ({ path: file.path.trim(), content: file.content }))
  )
}
</script>

<template>
  <AdaptiveDialog
    v-model="open"
    :title="t('tasks.judge.trial.title')"
    :primary-label="trial ? t('tasks.judge.trial.again') : t('tasks.judge.trial.run')"
    :primary-loading="busy"
    :primary-disabled="!ready"
    :cancel-label="t('tasks.judge.trial.close')"
    size="lg"
    persistent
    @primary="start"
  >
    <div class="ttt" data-testid="task-test-trial">
      <p class="ttt__hint t-meta-read">{{ t('tasks.judge.trial.hint') }}</p>
      <div v-for="(file, index) in files" :key="index" class="ttt__file">
        <div class="ttt__file-head">
          <v-text-field
            :model-value="file.path"
            :aria-label="t('tasks.judge.trial.path', { n: index + 1 })"
            :placeholder="t('tasks.judge.trial.pathPlaceholder')"
            autocomplete="off"
            maxlength="255"
            variant="outlined"
            density="compact"
            hide-details
            :data-testid="`task-test-trial-path-${index}`"
            @update:model-value="setFile(index, { path: String($event ?? '') })"
          />
          <BaseButton
            kind="ghost"
            size="sm"
            icon="mdi-close"
            :disabled="files.length < 2"
            :aria-label="t('tasks.judge.trial.removeFile', { n: index + 1 })"
            @click="removeFile(index)"
          />
        </div>
        <BaseField :label="t('tasks.judge.trial.content')">
          <v-textarea
            :model-value="file.content"
            rows="6"
            auto-grow
            max-rows="16"
            variant="outlined"
            density="compact"
            hide-details
            class="ttt__code"
            :data-testid="`task-test-trial-content-${index}`"
            @update:model-value="setFile(index, { content: String($event ?? '') })"
          />
        </BaseField>
      </div>
      <BaseButton kind="secondary" size="sm" prepend-icon="mdi-plus" class="ttt__add" @click="addFile">{{
        t('tasks.judge.trial.addFile')
      }}</BaseButton>

      <p v-if="error" class="ttt__error" role="alert" data-testid="task-test-trial-error">{{ error }}</p>
      <section v-if="trial" class="ttt__result" :aria-label="t('tasks.judge.trial.result')">
        <span class="ttt__label">{{ t('tasks.judge.trial.result') }}</span>
        <TaskJudgeResult :run="trial" teacher :slowest-ms="trial.slowestMs" :time-limit-ms="trial.timeLimitMs" />
      </section>
    </div>
  </AdaptiveDialog>
</template>

<style scoped>
.ttt {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.ttt__hint {
  margin: 0;
}

.ttt__file {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.ttt__file-head {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 32px;
  gap: 8px;
  align-items: center;
}

.ttt__code :deep(textarea) {
  font-family: var(--font-mono);
  font-size: 13px;
}

.ttt__add {
  align-self: flex-start;
}

.ttt__error {
  margin: 0;
  color: var(--danger-ink);
}

.ttt__result {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-top: 12px;
  border-top: 1px solid var(--line-2);
}

.ttt__label {
  color: var(--ink);
  font-size: 13px;
  font-weight: 500;
}
</style>
