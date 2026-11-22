<script setup lang="ts">
// 领题项目总览上的「学情分析与巩固练习」卡：学生领了题、在自己的项目里做作业，最该补的
// 知识点和到期要复习的巩固题也在这里说一句，一点就进「我的学习洞察」，不用先退出项目、
// 回空间、再从侧栏进去。做完巩固题能从那一页回到这道题接着交。
//
// 只在领题来的项目上摆（普通项目没有这道题，也就没有学情分析可看），开关读不到就不摆：
// 不替一块不认识的板下结论（与 `useSpaceLearningAnalysis` 同一个口径）。
import { computed } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

export interface ProjectLearning {
  /** 最该补的那个知识点；还没有分析结果时是 null。 */
  focus: { name: string; level: 'weak' | 'shaky' | 'mastered' } | null
  /** 到期的复习、还没做完的巩固题各几组。 */
  due: number
  open: number
  /** 今天还能生成几组；读不到是 null。 */
  left: number | null
}

const props = defineProps<{ learning: ProjectLearning }>()
const emit = defineEmits<{ 'open-learning': [] }>()

const summary = computed(() => {
  const parts: string[] = []
  if (props.learning.focus) {
    parts.push(
      t('work.overview.learningFocus', {
        name: props.learning.focus.name,
        level: t(`spaces.learningAnalysis.insight.levels.${props.learning.focus.level}`),
      })
    )
  }
  if (props.learning.due > 0) parts.push(t('work.overview.learningDue', { n: props.learning.due }))
  if (props.learning.open > 0) parts.push(t('work.overview.learningOpen', { n: props.learning.open }))
  return parts
})
</script>

<template>
  <section class="learn" data-testid="overview-learning">
    <div class="learn__head">
      <h2 class="learn__title">{{ t('work.overview.learningTitle') }}</h2>
      <BaseButton kind="secondary" size="sm" data-testid="overview-learning-go" @click="emit('open-learning')">
        {{
          learning.focus || learning.due > 0 || learning.open > 0
            ? t('work.overview.learningGo')
            : t('work.overview.learningOpenInsight')
        }}
      </BaseButton>
    </div>
    <p v-if="summary.length" class="learn__line t-body">
      <span v-for="(part, i) in summary" :key="i" class="learn__part">{{ part }}</span>
    </p>
    <p v-else class="learn__line t-body c-muted">{{ t('work.overview.learningEmpty') }}</p>
    <p v-if="learning.left !== null && learning.focus" class="learn__note t-meta c-muted">
      {{ t('spaces.learningAnalysis.practice.left', { n: learning.left }) }}
    </p>
  </section>
</template>

<style scoped>
.learn {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 16px 24px 14px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  margin-bottom: 16px;
}
.learn__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.learn__title {
  margin: 0;
  font-size: 15px;
  line-height: var(--lh-15);
  font-weight: 600;
  color: var(--ink);
}
.learn__line {
  margin: 0;
  display: flex;
  gap: 4px 14px;
  flex-wrap: wrap;
}
.learn__part {
  white-space: normal;
}
.learn__note {
  margin: 0;
}
</style>
