<template>
  <div class="an-section">
    <template v-if="enabled === false">
      <section class="an-card">
        <h3 class="an-card__title">{{ t('spaces.analytics.learning.class.off.title') }}</h3>
        <p class="an-note">{{ t('spaces.analytics.learning.class.off.desc') }}</p>
      </section>
      <slot name="legacy" />
    </template>

    <template v-else>
      <div class="la-head">
        <div class="la-tabs" role="tablist" :aria-label="t('spaces.analytics.learning.class.subsLabel')">
          <button
            v-for="key in SUBS"
            :key="key"
            type="button"
            role="tab"
            class="an-toggle"
            :class="{ 'an-toggle--on': key === sub && pointId == null }"
            :aria-selected="key === sub && pointId == null"
            @click="choose(key)"
          >
            {{ t(`spaces.analytics.learning.class.sub.${key}`) }}
          </button>
        </div>
        <BaseButton kind="secondary" @click="emit('export', pointId)">
          {{ t('spaces.analytics.learning.class.export') }}
        </BaseButton>
      </div>
      <p v-if="pointId == null" class="an-note">{{ t(`spaces.analytics.learning.class.lede.${sub}`) }}</p>

      <BaseLoadError
        v-if="failed"
        :title="t('spaces.analytics.learning.class.loadFailed')"
        :error="errorDetail"
        @retry="emit('retry')"
      />
      <v-progress-linear v-else-if="loading && !current" indeterminate color="primary" />

      <template v-if="pointId != null">
        <LearningClassPointDetail
          v-if="pointDetail && !failed"
          :detail="pointDetail"
          :pushing="pushing"
          :push-result="pushResult"
          @push="(id) => emit('push', id)"
          @back="emit('closePoint')"
          @open-excerpts="(group) => emit('openExcerpts', group)"
          @export="(id) => emit('export', id)"
        />
      </template>

      <template v-else-if="sub === 'overview'">
        <LearningClassOverview
          v-if="overview && !failed"
          :overview="overview"
          @open-point="(id) => emit('openPoint', id)"
        />
        <!-- 原有的发言与队列不依赖上面那一半：学情分析没读到时它照样在。 -->
        <h3 class="la-legacy-title">{{ t('spaces.analytics.learning.class.overview.legacyTitle') }}</h3>
        <slot name="legacy" />
      </template>
      <LearningClassPoints
        v-else-if="!failed && sub === 'points' && points"
        :data="points"
        @open-point="(id) => emit('openPoint', id)"
        @ratios="(asked, stuck) => emit('ratios', asked, stuck)"
      />
      <LearningClassQuestions
        v-else-if="!failed && sub === 'questions' && questions"
        :data="questions"
        @open-excerpts="(group) => emit('openExcerpts', group)"
      />
      <LearningClassWords v-else-if="!failed && sub === 'words' && words" :data="words" />
      <LearningClassTasks v-else-if="!failed && sub === 'tasks' && tasks" :data="tasks" />
      <LearningClassBehavior v-else-if="!failed && sub === 'behavior' && behavior" :data="behavior" />
      <LearningClassTrend v-else-if="!failed && sub === 'trend' && trend" :data="trend" />
    </template>
  </div>
</template>

<script setup lang="ts">
// 「数据 › 学情分析」的画面：七个子页签、右上「导出报告」、知识点详情，以及总览下方原有的
// 发言与队列（由容器经 `legacy` 插槽递进来）。只收 props、把要办的事交上去 —— 取数、
// 导出、摘录弹窗都在容器 `Learning.vue`。
//
// 空间没开学情分析（`enabled === false`）：只显示一段去设置里开启的提示，和原有内容。
import type {
  LearningClassBehavior as Behavior,
  LearningClassOverview as Overview,
  LearningClassPointDetail as PointDetail,
  LearningClassPoints as Points,
  LearningClassQuestions as Questions,
  LearningClassTasks as Tasks,
  LearningClassTrend as Trend,
  LearningClassWords as Words,
  LearningPushResult,
} from '@/types/apiSpaces'
import type { LearningSub } from './learningSubs'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import LearningClassBehavior from './components/LearningClassBehavior.vue'
import LearningClassOverview from './components/LearningClassOverview.vue'
import LearningClassPointDetail from './components/LearningClassPointDetail.vue'
import LearningClassPoints from './components/LearningClassPoints.vue'
import LearningClassQuestions from './components/LearningClassQuestions.vue'
import LearningClassTasks from './components/LearningClassTasks.vue'
import LearningClassTrend from './components/LearningClassTrend.vue'
import LearningClassWords from './components/LearningClassWords.vue'
import { LEARNING_SUBS as SUBS } from './learningSubs'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'

const props = withDefaults(
  defineProps<{
    sub: LearningSub
    /** 打开着的知识点详情；为空时显示子页签本身。 */
    pointId?: number | null
    /** 空间开没开学情分析；还不知道时为空。 */
    enabled: boolean | null
    loading?: boolean
    failed?: boolean
    errorDetail?: string | null
    overview?: Overview | null
    points?: Points | null
    pointDetail?: PointDetail | null
    /** 知识点详情里「推送巩固题」：正在推、刚推回来的结果。 */
    pushing?: boolean
    pushResult?: LearningPushResult | null
    questions?: Questions | null
    words?: Words | null
    tasks?: Tasks | null
    behavior?: Behavior | null
    trend?: Trend | null
  }>(),
  {
    pointId: null,
    loading: false,
    failed: false,
    errorDetail: null,
    overview: null,
    points: null,
    pointDetail: null,
    pushing: false,
    pushResult: null,
    questions: null,
    words: null,
    tasks: null,
    behavior: null,
    trend: null,
  }
)

const emit = defineEmits<{
  'update:sub': [sub: LearningSub]
  openPoint: [pointId: number]
  closePoint: []
  openExcerpts: [group: { cluster: number } | { misconception: number }]
  /** 导出报告；从知识点详情点进来时带上那个知识点。 */
  export: [pointId: number | null]
  ratios: [asked: number, stuck: number]
  push: [pointId: number]
  retry: []
}>()

const { t } = useI18n()

const current = computed(() => {
  if (props.pointId != null) return props.pointDetail
  return {
    overview: props.overview,
    points: props.points,
    questions: props.questions,
    words: props.words,
    tasks: props.tasks,
    behavior: props.behavior,
    trend: props.trend,
  }[props.sub]
})

const choose = (key: LearningSub) => {
  if (props.pointId != null) emit('closePoint')
  emit('update:sub', key)
}
</script>

<style scoped src="./analytics.css"></style>

<style scoped>
.la-head {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  justify-content: space-between;
  align-items: center;
}

.la-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.la-legacy-title {
  margin: 8px 0 0;
  color: var(--ink);
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15);
}
</style>
