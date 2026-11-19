<template>
  <section class="an-card">
    <p v-if="!data.tasks.length" class="an-note">{{ t('spaces.analytics.learning.class.tasks.empty') }}</p>
    <table v-else class="lc-table">
      <thead>
        <tr>
          <th v-for="key in columns" :key="key">{{ t(`spaces.analytics.learning.class.tasks.col.${key}`) }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="task in data.tasks" :key="task.taskId">
          <td>{{ task.name }}</td>
          <template v-if="task.analysed">
            <td>{{ task.claims }}</td>
            <td>{{ pct(task.completionRate) }}</td>
            <td>{{ task.avgTurns ?? '—' }}</td>
            <td>
              {{ task.avgHours == null ? '—' : t('spaces.analytics.learning.class.tasks.hours', { n: task.avgHours }) }}
            </td>
            <td>
              <span :class="{ 'lc-chip lc-chip--danger': (task.stuckRate ?? 0) >= 0.5 }">{{
                pct(task.stuckRate)
              }}</span>
            </td>
            <td>{{ t('spaces.analytics.learning.class.tasks.people', { n: task.answerSeekers ?? 0 }) }}</td>
            <td v-if="hasTests">{{ pct(task.testPassRate ?? null) }}</td>
          </template>
          <td v-else :colspan="hasTests ? 7 : 6" class="lc-muted">
            {{ t('spaces.analytics.learning.class.notAnalysed') }}
          </td>
        </tr>
      </tbody>
    </table>
    <p class="an-note lc-gap">{{ t('spaces.analytics.learning.class.tasks.seekersHint') }}</p>
    <p v-if="hasTests" class="an-note">{{ t('spaces.analytics.learning.class.tasks.testsHint') }}</p>
  </section>
</template>

<script setup lang="ts">
// 题目难度：每道题的完成率、平均对话轮数、平均用时、卡住率、代写迹象人数；有题挂着测试点
// 时再多一列全班的测试平均通过率（不足 3 份后端不给，显示「—」）。
import type { LearningClassTasks } from '@/types/apiSpaces'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps<{ data: LearningClassTasks }>()
const { t } = useI18n()

const COLUMNS = ['name', 'claims', 'completion', 'turns', 'hours', 'stuck', 'seekers'] as const
/** 有一道题评过测试才多这一列：没挂测试点的空间看到的表和原来一样。 */
const hasTests = computed(() => props.data.tasks.some((task) => task.testJudged != null))
const columns = computed(() => (hasTests.value ? [...COLUMNS, 'tests' as const] : COLUMNS))
const pct = (value: number | null) => (value == null ? '—' : `${Math.round(value * 100)}%`)
</script>

<style scoped src="../analytics.css"></style>
<style scoped src="./learningClass.css"></style>
