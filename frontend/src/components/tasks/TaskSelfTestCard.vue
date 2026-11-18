<script setup lang="ts">
// 提交页上的「自测样例」（方案 §自测样例）：出题人给这一阶段的输入/输出用例开了自测
// （`selfTestDaily`），学生交之前可以拿项目默认分支现在的代码只跑样例，不计分、不算交
// 作业，每天有次数。
//
// 按钮什么时候点不了：过了截止（`closed`）、今天的次数用完、上一次还在跑。排不上时后端
// 那句话（`error`）照原样显示。结果只有样例，输入输出本来就公开，按老师那一份画。
//
// 它只画：取数和排队在页面那一层（`useSelfTest`），这里点一下往上报。
import type { TaskSelfTestView, TaskTestCase } from '@/types'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import TaskJudgeResult from './TaskJudgeResult.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { monthDayTime } from '@/lib/dateText'

const props = withDefaults(
  defineProps<{
    view: TaskSelfTestView | null
    /** 正在排这一次。 */
    starting?: boolean
    /** 排不上时后端说的那句话。 */
    error?: string
    /** 过了截止（这一阶段或这份领题）：不能再自测。 */
    closed?: boolean
    /** 这一阶段的样例（题目上那一份），用来对上输入和期望输出。 */
    samples?: TaskTestCase[]
    suiteRevision?: number | null
  }>(),
  { starting: false, error: '', closed: false, samples: () => [], suiteRevision: null }
)

const emit = defineEmits<{ run: [] }>()

const { t } = useI18n()

const run = computed(() => props.view?.selfTest ?? null)
const inFlight = computed(() => run.value?.state === 'queued' || run.value?.state === 'running')
const limit = computed(() => props.view?.dailyLimit ?? null)
const left = computed(() => (limit.value == null ? 0 : Math.max(0, limit.value - (props.view?.usedToday ?? 0))))
const usedUp = computed(() => limit.value != null && left.value === 0)
const disabled = computed(() => props.closed || !props.view || limit.value == null || usedUp.value || inFlight.value)

const countText = computed(() => {
  if (props.closed) return t('tasks.judge.selfTest.pastDue')
  if (limit.value == null) return ''
  if (usedUp.value) {
    return t('tasks.judge.selfTest.usedUp', { limit: limit.value, when: monthDayTime(props.view?.resetsAt ?? 0) })
  }
  return t('tasks.judge.selfTest.remaining', { left: left.value, limit: limit.value })
})
</script>

<template>
  <section class="tst" :aria-label="t('tasks.judge.selfTest.title')" data-testid="task-self-test">
    <div class="tst__head">
      <div class="tst__text">
        <span class="tst__title">{{ t('tasks.judge.selfTest.title') }}</span>
        <span class="t-meta-read">{{ t('tasks.judge.selfTest.hint') }}</span>
      </div>
      <BaseButton
        kind="secondary"
        size="sm"
        prepend-icon="mdi-play-outline"
        :loading="starting || inFlight"
        :disabled="disabled"
        data-testid="task-self-test-run"
        @click="emit('run')"
        >{{ t('tasks.judge.selfTest.run') }}</BaseButton
      >
    </div>
    <p v-if="countText" class="tst__count t-meta-read" data-testid="task-self-test-count">{{ countText }}</p>
    <p v-if="error" class="tst__error" role="alert" data-testid="task-self-test-error">{{ error }}</p>
    <template v-if="run">
      <p v-if="run.codeSha" class="tst__count t-meta-read" data-testid="task-self-test-sha">
        {{ t('tasks.judge.selfTest.ran', { sha: run.codeSha.slice(0, 8) }) }}
      </p>
      <TaskJudgeResult :run="run" teacher kind="IO" :samples="samples" :suite-revision="suiteRevision" />
    </template>
  </section>
</template>

<style scoped>
.tst {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 16px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
}

.tst__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.tst__text {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.tst__title {
  color: var(--ink);
  font-size: 14px;
  font-weight: 600;
}

.tst__count {
  margin: 0;
}

.tst__error {
  margin: 0;
  color: var(--danger-ink);
  font-size: 13px;
}
</style>
