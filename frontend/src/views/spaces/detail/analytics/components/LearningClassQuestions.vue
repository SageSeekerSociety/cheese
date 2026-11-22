<template>
  <section class="an-card">
    <div class="lc-row">
      <h3 class="an-card__title">{{ t('spaces.analytics.learning.class.questions.title') }}</h3>
      <span class="lc-muted">{{
        t('spaces.analytics.learning.class.questions.summary', {
          questions: data.totalQuestions,
          clusters: data.totalClusters,
          shown: data.clusters.length,
        })
      }}</span>
    </div>
    <p v-if="!data.clusters.length" class="an-note">{{ t('spaces.analytics.learning.class.questions.empty') }}</p>
    <div v-for="(cluster, index) in data.clusters" :key="cluster.id" class="lc-item">
      <div class="lc-row">
        <div>
          <span class="lc-item__title">{{ index + 1 }}. {{ cluster.label }}</span>
          <span v-if="cluster.trend" class="lc-chip lc-trend" :class="TREND_TONE[cluster.trend]">{{
            t(`spaces.analytics.learning.class.questions.trend.${cluster.trend}`)
          }}</span>
          <div class="lc-muted">
            {{ cluster.knowledgePointName ?? t('spaces.analytics.learning.class.questions.noPoint') }} ·
            {{ t('spaces.analytics.learning.class.questions.meta', { people: cluster.people, asks: cluster.asks }) }}
          </div>
          <p v-if="cluster.example" class="lc-quote">
            <span class="lc-code">{{
              t('spaces.analytics.learning.class.memberCode', { code: cluster.example.code })
            }}</span
            >：{{ cluster.example.quote }}
          </p>
        </div>
        <BaseButton kind="secondary" size="sm" @click="emit('openExcerpts', { cluster: cluster.id })">
          {{ t('spaces.analytics.learning.class.questions.viewExcerpts') }}
        </BaseButton>
      </div>
    </div>
    <p v-if="data.hiddenClusters" class="an-note lc-gap">
      {{ t('spaces.analytics.learning.class.questions.hidden', { n: data.hiddenClusters }) }}
    </p>
  </section>
</template>

<script setup lang="ts">
// 高频问题：AI 归好的问题类，按问过的人数排；和上周比的变化；一句代号例句；点开看匿名对话。
import type { LearningClassQuestions } from '@/types/apiSpaces'

import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'

defineProps<{ data: LearningClassQuestions }>()
const emit = defineEmits<{ openExcerpts: [group: { cluster: number }] }>()
const { t } = useI18n()

const TREND_TONE = { up: 'lc-chip--danger', down: 'lc-chip--ok', new: 'lc-chip--accent' } as const
</script>

<style scoped src="../analytics.css"></style>
<style scoped src="./learningClass.css"></style>

<style scoped>
.lc-trend {
  margin-left: 8px;
}
</style>
