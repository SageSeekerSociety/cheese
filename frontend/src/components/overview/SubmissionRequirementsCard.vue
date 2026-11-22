<script setup lang="ts">
// 领题项目总览顶部的「提交要求」卡：按题目的提交项逐条列出，每条给去处；卡底是统一
// 的提交入口，和这个身份最近交的一版、那一版的测试结果。第一版不在总览里直接写文本：
// 文本项和统一入口都跳到题目页的提交表单（提交、评测、评分都沿用那里，这里不另做）。
// 测试点是挂在题目上的、评测的是项目默认分支，所以「写代码」按这道题挂没挂测试点判断，
// 不按课程、也不按语言。
import type { TaskSubmissionEntryType } from '@/types'

import { computed } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'
import { relTime } from '@/lib/relTime'

export interface SubmissionRequirement {
  prompt: string
  type: TaskSubmissionEntryType
}
export interface LatestHandIn {
  version: number
  createdAt: number
  /** 这一版的评测摘要；题目没挂测试点、或还没读到，是 null。 */
  tests: { state: 'queued' | 'running' | 'done' | 'failed'; passed: number; total: number } | null
}

const props = defineProps<{
  entries: SubmissionRequirement[]
  /** 这道题挂了测试点：交的那一刻平台拿项目默认分支的代码去评测。 */
  hasTests: boolean
  /** 题目现在还收提交。 */
  submittable: boolean
  /** 我最近交的一版；没交过是 null。 */
  latest: LatestHandIn | null
}>()
const emit = defineEmits<{ 'go-submit': []; 'new-task': [] }>()

// 有测试点时，文件项交的就是代码：入口是去写，不是在这里传。
const rows = computed(() =>
  props.entries.map((entry) => ({
    ...entry,
    kind:
      entry.type === 'FILE' && props.hasTests
        ? ('code' as const)
        : entry.type === 'FILE'
          ? ('file' as const)
          : ('text' as const),
  }))
)
const hasCodeRow = computed(() => rows.value.some((row) => row.kind === 'code'))
const testsText = computed(() => {
  const tests = props.latest?.tests
  if (!tests) return ''
  if (tests.state === 'queued' || tests.state === 'running') return t('work.overview.submitTestsRunning')
  if (tests.state === 'failed') return t('work.overview.submitTestsFailed')
  return t('work.overview.submitTestsDone', { passed: tests.passed, total: tests.total })
})
</script>

<template>
  <section class="sreq" data-testid="overview-submission">
    <h2 class="sreq__title">{{ t('work.overview.submitTitle') }}</h2>
    <ul class="sreq__rows">
      <li v-for="(row, i) in rows" :key="i" class="sreq__row" :data-testid="`submission-row-${row.kind}`">
        <span class="sreq__prompt t-body" data-user-content>{{ row.prompt }}</span>
        <span class="sreq__kind t-meta c-muted">{{ t(`work.overview.submitKind.${row.kind}`) }}</span>
        <BaseButton
          v-if="row.kind === 'code'"
          kind="secondary"
          size="sm"
          data-testid="submission-new-task"
          @click="emit('new-task')"
        >
          {{ t('work.overview.submitNewTask') }}
        </BaseButton>
        <BaseButton
          v-else
          kind="secondary"
          size="sm"
          data-testid="submission-row-go"
          :disabled="!submittable"
          @click="emit('go-submit')"
        >
          {{ t('work.overview.submitGo') }}
        </BaseButton>
      </li>
      <li v-if="hasTests && !hasCodeRow" class="sreq__row" data-testid="submission-row-code">
        <span class="sreq__prompt t-body">{{ t('work.overview.submitCodeOnly') }}</span>
        <span class="sreq__kind t-meta c-muted">{{ t('work.overview.submitKind.code') }}</span>
        <BaseButton kind="secondary" size="sm" data-testid="submission-new-task" @click="emit('new-task')">
          {{ t('work.overview.submitNewTask') }}
        </BaseButton>
      </li>
    </ul>
    <p v-if="hasTests" class="sreq__note t-meta c-muted">{{ t('work.overview.submitCodeHint') }}</p>
    <div class="sreq__foot">
      <div class="sreq__latest t-body" data-testid="submission-latest">
        <template v-if="latest">
          {{
            t('work.overview.submitLatest', {
              version: latest.version,
              when: relTime(new Date(latest.createdAt).toISOString()),
            })
          }}
          <span v-if="testsText" data-testid="submission-tests"> · {{ testsText }}</span>
        </template>
        <span v-else class="c-muted">{{ t('work.overview.submitNone') }}</span>
      </div>
      <BaseButton kind="primary" data-testid="submission-go" :disabled="!submittable" @click="emit('go-submit')">
        {{ submittable ? t('work.overview.submitGo') : t('work.overview.submitClosed') }}
      </BaseButton>
    </div>
  </section>
</template>

<style scoped>
.sreq {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 20px 24px 18px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  margin-bottom: 16px;
}
.sreq__title {
  margin: 0;
  font-size: 15px;
  line-height: var(--lh-15);
  font-weight: 600;
  color: var(--ink);
}
.sreq__rows {
  list-style: none;
  margin: 0;
  padding: 0;
}
.sreq__row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 0;
  border-bottom: 1px solid var(--line-2);
}
.sreq__prompt {
  flex: 1 1 auto;
  min-width: 0;
  overflow-wrap: anywhere;
}
.sreq__kind {
  flex: none;
}
.sreq__note {
  margin: 0;
}
.sreq__foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
</style>
