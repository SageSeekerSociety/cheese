<template>
  <div class="lc-overview">
    <div class="lc-stats">
      <div class="lc-stat">
        <div class="lc-stat__label">{{ t('spaces.analytics.learning.class.overview.completionRate') }}</div>
        <div class="lc-stat__value">{{ percent(overview.completion.rate) }}</div>
        <div class="lc-stat__hint">{{ t('spaces.analytics.learning.class.overview.completionHint') }}</div>
      </div>
      <div class="lc-stat">
        <div class="lc-stat__label">{{ t('spaces.analytics.learning.class.overview.members') }}</div>
        <div class="lc-stat__value">{{ formatCount(overview.members) }}</div>
        <div class="lc-stat__hint">{{ t('spaces.analytics.learning.class.overview.membersHint') }}</div>
      </div>
      <div class="lc-stat">
        <div class="lc-stat__label">{{ t('spaces.analytics.learning.class.overview.messages') }}</div>
        <div class="lc-stat__value">{{ formatCount(overview.messagesThisWeek) }}</div>
        <div class="lc-stat__hint">{{ t('spaces.analytics.learning.class.overview.messagesHint') }}</div>
      </div>
      <div class="lc-stat">
        <div class="lc-stat__label">{{ t('spaces.analytics.learning.class.overview.weak') }}</div>
        <div class="lc-stat__value">{{ formatCount(overview.weakPoints) }}</div>
        <div class="lc-stat__hint">{{ t('spaces.analytics.learning.class.overview.weakHint') }}</div>
      </div>
    </div>

    <p class="an-note">
      {{
        overview.updatedAt
          ? t('spaces.analytics.learning.class.overview.updated', { time: formatLearningTime(overview.updatedAt) })
          : t('spaces.analytics.learning.class.overview.notYet')
      }}
    </p>

    <div class="an-grid">
      <section class="an-card">
        <h3 class="an-card__title">
          {{ t('spaces.analytics.learning.class.overview.tasksTitle') }}
          <span class="lc-muted">{{
            t('spaces.analytics.learning.class.overview.tasksHint', { n: overview.members })
          }}</span>
        </h3>
        <p v-if="!overview.tasks.length" class="an-note">
          {{ t('spaces.analytics.learning.class.overview.tasksEmpty') }}
        </p>
        <div v-for="task in overview.tasks" :key="task.taskId" class="lc-task">
          <span class="lc-task__name">{{ task.name }}</span>
          <div v-if="task.analysed" class="lc-bar" role="img" :aria-label="taskLabel(task)">
            <span
              v-for="part in parts(task)"
              :key="part.key"
              class="lc-bar__part"
              :class="`lc-bar__part--${part.key}`"
              :style="{ width: `${part.width}%` }"
            />
          </div>
          <span v-else class="lc-muted">{{ t('spaces.analytics.learning.class.notAnalysed') }}</span>
        </div>
        <div class="lc-legend">
          <span v-for="key in STATUS" :key="key" class="lc-legend__item">
            <span class="lc-swatch" :class="`lc-bar__part--${key}`" />
            {{ t(`spaces.analytics.learning.class.overview.status.${key}`) }}
          </span>
        </div>
      </section>

      <section class="an-card">
        <h3 class="an-card__title">{{ t('spaces.analytics.learning.class.overview.focusTitle') }}</h3>
        <p v-if="!overview.focus.length" class="an-note">
          {{ t('spaces.analytics.learning.class.overview.focusEmpty') }}
        </p>
        <div v-for="point in overview.focus" :key="point.knowledgePointId" class="lc-focus">
          <div class="lc-focus__head">
            <span class="lc-focus__name">{{ point.name }}</span>
            <span class="lc-muted">{{
              t('spaces.analytics.learning.class.overview.focusStuck', {
                stuck: point.stuck,
                involved: point.involved,
              })
            }}</span>
          </div>
          <div class="lc-bar">
            <span
              class="lc-bar__part lc-bar__part--stuck"
              :style="{ width: `${point.involved ? (point.stuck / point.involved) * 100 : 0}%` }"
            />
          </div>
          <BaseButton kind="ghost" size="sm" @click="emit('openPoint', point.knowledgePointId)">
            {{ t('spaces.analytics.learning.class.overview.focusLink') }}
          </BaseButton>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
// 总览：四个数、按题的完成分布、下节课值得讲的三个知识点。数据进、点哪个知识点出，不取数。
import type { LearningClassOverview, LearningClassTaskCompletion } from '@/types/apiSpaces'

import { useI18n } from 'vue-i18n'

import { formatCount, formatLearningTime } from '../helpers'

import BaseButton from '@/components/base/BaseButton.vue'

defineProps<{ overview: LearningClassOverview }>()
const emit = defineEmits<{ openPoint: [pointId: number] }>()
const { t } = useI18n()

const STATUS = ['notStarted', 'inProgress', 'submitted', 'passed'] as const

const percent = (value: number | null) => (value == null ? '—' : `${Math.round(value * 100)}%`)

const parts = (task: LearningClassTaskCompletion) => {
  const values = STATUS.map((key) => task[key] ?? 0)
  const total = values.reduce((a, b) => a + b, 0) || 1
  return STATUS.map((key, index) => ({ key, width: (values[index] / total) * 100 }))
}

const taskLabel = (task: LearningClassTaskCompletion) =>
  STATUS.map((key) => `${t(`spaces.analytics.learning.class.overview.status.${key}`)} ${task[key] ?? 0}`).join(' · ')
</script>

<style scoped src="../analytics.css"></style>

<style scoped>
.lc-overview {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.lc-stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 12px;
}

.lc-stat {
  padding: 12px 16px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}

.lc-stat__label,
.lc-stat__hint,
.lc-muted {
  color: var(--muted);
  font-size: 13px;
  font-weight: 400;
  line-height: var(--lh-13);
}

.lc-stat__value {
  margin: 4px 0;
  color: var(--ink);
  font-size: 18px;
  font-weight: 600;
  line-height: var(--lh-18);
  font-variant-numeric: tabular-nums;
}

.lc-task {
  display: grid;
  grid-template-columns: minmax(80px, 140px) 1fr;
  gap: 12px;
  align-items: center;
  padding: 6px 0;
}

.lc-task__name {
  color: var(--text);
  font-size: 13px;
  line-height: var(--lh-13);
  overflow-wrap: anywhere;
}

.lc-bar {
  display: flex;
  height: 12px;
  overflow: hidden;
  border-radius: var(--radius-pill);
  background: var(--fill);
}

.lc-bar__part--notStarted {
  background: var(--fill-2);
}

.lc-bar__part--inProgress {
  background: var(--warn);
}

/* 已提交用蓝（--chart-1）。原来取的 --chart-2 是青，和相邻的已通过（--ok 的绿）明度
   太近：12px 的条上两段几乎连成一片，底下图例的两个色块也难分。「已提交」没有空着的
   状态色可用（--warn 归进行中、--ok 归已通过），这条完成条一直从图表色族取色，这里
   换成同族里与绿拉得开的蓝，深浅两套主题各有一档现成的值（style.css 的 --chart-1）。 */
.lc-bar__part--submitted {
  background: var(--chart-1);
}

.lc-bar__part--passed {
  background: var(--ok);
}

.lc-bar__part--stuck {
  background: var(--danger);
}

.lc-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 8px;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}

.lc-legend__item {
  display: inline-flex;
  gap: 6px;
  align-items: center;
}

.lc-swatch {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: var(--radius-sm);
}

.lc-focus {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px 0;
}

.lc-focus + .lc-focus {
  border-top: 1px solid var(--line);
}

.lc-focus__head {
  display: flex;
  gap: 8px;
  justify-content: space-between;
}

.lc-focus__name {
  color: var(--ink);
  font-size: 14px;
  font-weight: 600;
  line-height: var(--lh-14);
}
</style>
