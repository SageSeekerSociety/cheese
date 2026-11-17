<template>
  <div class="lc-page">
    <div>
      <BaseButton kind="ghost" size="sm" @click="emit('back')">
        {{ t('spaces.analytics.learning.class.detail.back') }}
      </BaseButton>
    </div>

    <div class="lc-detail">
      <div class="lc-page">
        <section class="an-card">
          <h3 class="lc-detail__title">{{ detail.knowledgePoint.name }}</h3>
          <p v-if="!detail.analysed" class="lc-muted">{{ t('spaces.analytics.learning.class.notAnalysed') }}</p>
          <p v-else class="lc-muted">
            {{
              t('spaces.analytics.learning.class.detail.stuckLine', { stuck: detail.stuck, involved: detail.involved })
            }}
            <template v-if="detail.tasks.length">
              · {{ t('spaces.analytics.learning.class.detail.tasksLine', { tasks: detail.tasks.join('、') }) }}
            </template>
            <template v-if="detail.newThisWeek">
              · {{ t('spaces.analytics.learning.class.detail.newLine', { n: detail.newThisWeek }) }}
            </template>
          </p>

          <template v-if="detail.analysed">
            <h4 class="an-card__title lc-gap">{{ t('spaces.analytics.learning.class.detail.misTitle') }}</h4>
            <p v-if="!detail.misconceptions.length" class="an-note">
              {{ t('spaces.analytics.learning.class.detail.misEmpty') }}
            </p>
            <div v-for="item in detail.misconceptions" :key="item.id" class="lc-item">
              <div class="lc-row">
                <span class="lc-item__title">{{ item.label }}</span>
                <span class="lc-chip lc-chip--danger">{{
                  t('spaces.analytics.learning.class.detail.misPeople', { n: item.people })
                }}</span>
              </div>
              <p v-for="(example, index) in item.examples" :key="index" class="lc-quote">
                <span class="lc-code">{{
                  t('spaces.analytics.learning.class.memberCode', { code: example.code })
                }}</span
                >：{{ example.quote }}
              </p>
              <BaseButton
                kind="ghost"
                size="sm"
                class="lc-gap"
                @click="emit('openExcerpts', { misconception: item.id })"
              >
                {{ t('spaces.analytics.learning.class.detail.viewExcerpts') }}
              </BaseButton>
            </div>
            <p v-if="detail.hiddenMisconceptions" class="an-note lc-gap">
              {{ t('spaces.analytics.learning.class.detail.misHidden', { n: detail.hiddenMisconceptions }) }}
            </p>
          </template>
        </section>

        <section v-if="detail.analysed && detail.mastery" class="an-card">
          <h4 class="an-card__title">{{ t('spaces.analytics.learning.class.detail.masteryTitle') }}</h4>
          <div class="lc-mastery" role="img" :aria-label="masteryLabel">
            <span
              v-for="part in masteryParts"
              :key="part.key"
              :class="`lc-mastery__${part.key}`"
              :style="{ width: `${part.width}%` }"
            />
          </div>
          <div class="lc-legend">
            <span v-for="part in masteryParts" :key="part.key" class="lc-muted">
              <span class="lc-swatch" :class="`lc-mastery__${part.key}`" />
              {{
                t('spaces.analytics.learning.class.detail.levelCount', {
                  level: t(`spaces.analytics.learning.class.detail.levels.${part.key}`),
                  n: part.count,
                })
              }}
            </span>
          </div>
          <p class="an-note lc-gap">{{ t('spaces.analytics.learning.class.detail.noList', { n: detail.stuck }) }}</p>
        </section>
      </div>

      <div class="lc-page">
        <section class="an-card">
          <h4 class="an-card__title">{{ t('spaces.analytics.learning.class.detail.adviceTitle') }}</h4>
          <p v-if="detail.advice" class="lc-advice">{{ detail.advice.text }}</p>
          <p v-else class="an-note">{{ t('spaces.analytics.learning.class.detail.adviceEmpty') }}</p>
          <BaseButton kind="secondary" size="sm" class="lc-gap" @click="emit('export', detail.knowledgePoint.id)">
            {{ t('spaces.analytics.learning.class.detail.exportPoint') }}
          </BaseButton>
        </section>
        <section class="an-card">
          <h4 class="an-card__title">{{ t('spaces.analytics.learning.class.detail.whoTitle') }}</h4>
          <p class="an-note">{{ t('spaces.analytics.learning.class.detail.whoBody') }}</p>
        </section>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
// 知识点详情：卡住几人、常见误解（按人数）与代号例句、掌握分布、讲课建议。不列名单。
import type { LearningClassPointDetail } from '@/types/apiSpaces'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'

const props = defineProps<{ detail: LearningClassPointDetail }>()
const emit = defineEmits<{
  back: []
  openExcerpts: [group: { misconception: number }]
  export: [pointId: number]
}>()
const { t } = useI18n()

const LEVELS = ['mastered', 'shaky', 'weak', 'untouched'] as const

const masteryParts = computed(() => {
  const mastery = props.detail.mastery
  if (!mastery) return []
  const total = LEVELS.reduce((sum, key) => sum + mastery[key], 0) || 1
  return LEVELS.map((key) => ({ key, count: mastery[key], width: (mastery[key] / total) * 100 }))
})

const masteryLabel = computed(() =>
  masteryParts.value
    .map((part) => `${t(`spaces.analytics.learning.class.detail.levels.${part.key}`)} ${part.count}`)
    .join(' · ')
)
</script>

<style scoped src="../analytics.css"></style>
<style scoped src="./learningClass.css"></style>

<style scoped>
.lc-detail {
  display: grid;
  grid-template-columns: minmax(0, 2fr) minmax(0, 1fr);
  gap: 16px;
}

@media (max-width: 900px) {
  .lc-detail {
    grid-template-columns: 1fr;
  }
}

.lc-detail__title {
  margin: 0 0 4px;
  color: var(--ink);
  font-size: 18px;
  font-weight: 600;
  line-height: var(--lh-18);
}

.lc-advice {
  margin: 0;
  color: var(--text);
  font-size: 14px;
  line-height: var(--lh-14);
  white-space: pre-wrap;
}

.lc-mastery {
  display: flex;
  height: 14px;
  overflow: hidden;
  border-radius: var(--radius-pill);
  background: var(--fill);
}

.lc-mastery__mastered {
  background: var(--ok);
}

.lc-mastery__shaky {
  background: var(--warn);
}

.lc-mastery__weak {
  background: var(--danger);
}

.lc-mastery__untouched {
  background: var(--fill-2);
}

.lc-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 8px;
}

.lc-swatch {
  display: inline-block;
  width: 10px;
  height: 10px;
  margin-right: 4px;
  border-radius: var(--radius-sm);
  vertical-align: -1px;
}
</style>
