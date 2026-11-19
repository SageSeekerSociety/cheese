<script setup lang="ts">
// 一版提交下面那一块「评测结果」（方案 §看结果、§批改窗口）：跑的是哪一版代码、用了多久，
// 然后是逐个用例的结果（`TaskJudgeResult`）。学生那边，老师还没批改时注明其余几分等老师
// 批改；老师那边每个用例都能点开。
import type { TaskJudgeRun, TaskTestSuite } from '@/types'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import TaskJudgeResult from './TaskJudgeResult.vue'

const props = withDefaults(
  defineProps<{
    run: TaskJudgeRun
    teacher?: boolean
    /** 题目上这个阶段挂的那一套（学生读到的样子），用来对上样例的输入和期望输出。 */
    suite?: TaskTestSuite | null
    /** 测试点那一行的分值；学生看不到评分要点时没有。 */
    linePoints?: number | null
    /** 老师还没批改时，其余几项合计几分（学生那边注明「等老师批改」）。 */
    waitingPoints?: number | null
  }>(),
  { teacher: false, suite: null, linePoints: null, waitingPoints: null }
)

const { t } = useI18n()

const seconds = computed(() =>
  props.run.machineMs != null ? Math.max(1, Math.round(props.run.machineMs / 1000)) : null
)
</script>

<template>
  <section class="tsj" :aria-label="t('tasks.judge.history.title')" data-testid="task-submission-judge">
    <div class="tsj__head">
      <span class="tsj__title">{{ t('tasks.judge.history.title') }}</span>
      <span class="t-meta-read">
        <template v-if="run.codeSha">
          {{ t('tasks.judge.history.code') }}
          <code class="tsj__sha">{{ run.codeSha.slice(0, 8) }}</code>
        </template>
        <template v-if="seconds != null"> · {{ t('tasks.judge.history.took', { s: seconds }) }}</template>
      </span>
    </div>
    <TaskJudgeResult
      :run="run"
      :teacher="teacher"
      :line-points="linePoints"
      :samples="suite?.cases ?? []"
      :suite-revision="suite?.revision ?? null"
      :kind="suite && suite.revision === run.suiteRevision ? suite.kind : null"
    >
      <template v-if="waitingPoints" #aside>
        <span class="tsj__waiting t-meta-read">{{ t('tasks.judge.history.waiting', { points: waitingPoints }) }}</span>
      </template>
    </TaskJudgeResult>
  </section>
</template>

<style scoped>
.tsj {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
}

.tsj__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
}

.tsj__title {
  color: var(--ink);
  font-weight: 500;
}

.tsj__sha {
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  background: var(--fill-2);
  font-family: var(--font-mono);
  font-size: 12px;
}

.tsj__waiting {
  align-self: flex-end;
  padding-bottom: 2px;
}
</style>
