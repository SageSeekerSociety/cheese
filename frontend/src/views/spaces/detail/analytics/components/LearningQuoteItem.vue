<template>
  <div class="quote-item">
    <v-checkbox
      v-if="selectable"
      v-model="checked"
      class="quote-item__check"
      density="compact"
      hide-details
      :aria-label="t('spaces.analytics.learning.quote.pick', { name: excerpt.studentName })"
    />

    <div class="quote-item__body">
      <p class="quote-item__quote">{{ excerpt.quote }}</p>
      <div class="quote-item__meta">
        <span class="quote-item__student">{{ excerpt.studentName }}</span>
        <span v-if="excerpt.knowledgePoint" class="chip-neutral">{{ excerpt.knowledgePoint }}</span>
        <span v-if="excerpt.topicTitle">{{ excerpt.topicTitle }}</span>
        <span class="quote-item__time">{{ formatLearningTime(excerpt.createdAt) }}</span>
      </div>
    </div>

    <BaseButton class="quote-item__source" kind="ghost" size="sm" @click="openSource">{{
      t('spaces.analytics.learning.quote.source')
    }}</BaseButton>
  </div>
</template>

<script setup lang="ts">
import type { SpaceLearningExcerpt } from '@/network/api/spaces/types'

import { useI18n } from 'vue-i18n'

import { useNavigation } from '@/composables/useNavigation'

import { buildSourceLink, formatLearningTime } from '../helpers'

import BaseButton from '@/components/base/BaseButton.vue'

const props = withDefaults(
  defineProps<{
    excerpt: SpaceLearningExcerpt
    selectable?: boolean
  }>(),
  { selectable: false }
)

const { t } = useI18n()

const checked = defineModel<boolean>('checked', { default: false })

const nav = useNavigation()

const openSource = () => {
  nav?.navigate(buildSourceLink(props.excerpt))
}
</script>

<style scoped lang="scss">
.quote-item {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  padding: 12px 0;

  & + & {
    border-top: 1px solid var(--line);
  }
}

.quote-item__check {
  flex: 0 0 auto;
  margin: 0;
}

.quote-item__body {
  flex: 1 1 auto;
  min-width: 0;
}

.quote-item__quote {
  margin: 0;
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--text);
  overflow-wrap: anywhere;
}

.quote-item__meta {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  align-items: center;
  margin-top: 8px;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--faint);
}

.quote-item__student {
  color: var(--muted);
}

.quote-item__source {
  flex: 0 0 auto;
}
</style>
