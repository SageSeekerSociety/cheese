<script setup lang="ts">
// 提交页那块「将要评测的代码」（方案 §提交）：项目默认分支现在指着的那一版，最后一次
// 提交的说明和推送时间。学生不上传代码，交的那一刻平台记下这一版，之后再推送也不影响
// 这一版的评测；本地还没推上去的改动不在里面，所以提示他先让芝士推送。
//
// 读不到（项目还没开出来、连的是 GitHub、托管方这一刻没答上来）时照常让他交，只说这一版
// 不会自动评测、由老师给测试分。
import type { TaskCodeVersion, TaskTestSuite } from '@/types'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'
import { calendarDaysAgo, clockTime, dayTimeText } from '@/lib/dateText'

const props = withDefaults(
  defineProps<{
    version: TaskCodeVersion | null
    loading?: boolean
    /** 这个阶段挂的测试点：用来说几个用例、计入哪一项多少分。 */
    suite: TaskTestSuite
    lineName?: string | null
    linePoints?: number | null
  }>(),
  { loading: false, lineName: null, linePoints: null }
)

const emit = defineEmits<{ refresh: [] }>()

const { t } = useI18n()

const short = computed(() => props.version?.sha.slice(0, 8) ?? '')
/** 推送时间：今天、昨天说词，再早给日期。 */
const pushed = computed(() => {
  const at = props.version?.pushedAt
  if (!at) return ''
  const days = calendarDaysAgo(at)
  if (days <= 0) return t('tasks.judge.submit.today', { time: clockTime(at) })
  if (days === 1) return t('tasks.judge.submit.yesterday', { time: clockTime(at) })
  return dayTimeText(at)
})
const samples = computed(() => props.suite.cases.filter((entry) => entry.visibility === 'SAMPLE').length)
</script>

<template>
  <section class="tcv" :aria-label="t('tasks.judge.submit.title')" data-testid="task-code-version">
    <div class="tcv__head">
      <span class="tcv__title">{{ t('tasks.judge.submit.title') }}</span>
      <BaseButton
        kind="ghost"
        size="sm"
        icon="mdi-refresh"
        :loading="loading"
        :aria-label="t('tasks.judge.submit.refresh')"
        :title="t('tasks.judge.submit.refresh')"
        @click="emit('refresh')"
      />
    </div>
    <p class="tcv__meta t-meta-read">
      <template v-if="suite.kind !== 'IO'">{{
        lineName && linePoints != null
          ? t('tasks.judge.submit.suiteOtherLine', {
              kind: t(`tasks.judge.kind.${suite.kind}`),
              name: lineName,
              points: linePoints,
            })
          : t('tasks.judge.submit.suiteOther', { kind: t(`tasks.judge.kind.${suite.kind}`) })
      }}</template>
      <template v-else>{{
        lineName && linePoints != null
          ? t('tasks.judge.submit.suiteLine', {
              n: suite.caseCount ?? suite.cases.length,
              samples,
              name: lineName,
              points: linePoints,
            })
          : t('tasks.judge.submit.suite', { n: suite.caseCount ?? suite.cases.length, samples })
      }}</template>
    </p>
    <template v-if="version">
      <div class="tcv__version">
        <v-icon size="16" icon="mdi-source-branch" aria-hidden="true" />
        <span class="tcv__branch" data-user-content>{{ version.branch }}</span>
        <code class="tcv__sha" data-testid="task-code-version-sha">{{ short }}</code>
        <span v-if="version.subject" class="tcv__subject" data-user-content>{{ version.subject }}</span>
        <span v-if="pushed" class="t-meta-read">{{ t('tasks.judge.submit.pushed', { when: pushed }) }}</span>
      </div>
      <p class="tcv__hint t-meta-read">{{ t('tasks.judge.submit.hint') }}</p>
    </template>
    <p v-else-if="!loading" class="tcv__missing" data-testid="task-code-version-missing">
      {{ t('tasks.judge.submit.missing') }}
    </p>
  </section>
</template>

<style scoped>
.tcv {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 12px 16px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
}

.tcv__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.tcv__title {
  color: var(--ink);
  font-weight: 500;
}

.tcv__meta,
.tcv__hint {
  margin: 0;
}

.tcv__version {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  color: var(--ink);
}

.tcv__sha {
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  background: var(--fill-2);
  font-family: var(--font-mono);
  font-size: 12px;
}

.tcv__subject {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tcv__missing {
  margin: 0;
  color: var(--warn-ink);
}
</style>
