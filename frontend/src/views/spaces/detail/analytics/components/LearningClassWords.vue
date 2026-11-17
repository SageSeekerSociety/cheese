<template>
  <div class="lc-page">
    <div class="an-grid">
      <section class="an-card">
        <h3 class="an-card__title">{{ t('spaces.analytics.learning.class.words.cloudTitle') }}</h3>
        <p v-if="!data.words.length" class="an-note">{{ t('spaces.analytics.learning.class.words.empty') }}</p>
        <div class="lc-cloud">
          <span
            v-for="(word, index) in data.words"
            :key="word.word"
            class="lc-cloud__word"
            :class="{ 'lc-cloud__word--new': word.isNew }"
            :style="{
              fontSize: `${size(word.count)}px`,
              color: word.isNew ? undefined : `var(--chart-${(index % 8) + 1})`,
            }"
          >
            {{ word.word }}<sup v-if="word.isNew"> {{ t('spaces.analytics.learning.class.words.newMark') }}</sup>
          </span>
        </div>
      </section>
      <section class="an-card">
        <h3 class="an-card__title">{{ t('spaces.analytics.learning.class.words.rankTitle') }}</h3>
        <table v-if="data.words.length" class="lc-table">
          <thead>
            <tr>
              <th>{{ t('spaces.analytics.learning.class.words.col.word') }}</th>
              <th>{{ t('spaces.analytics.learning.class.words.col.count') }}</th>
              <th>{{ t('spaces.analytics.learning.class.words.col.people') }}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            <tr v-for="word in data.words.slice(0, 10)" :key="word.word">
              <td>{{ word.word }}</td>
              <td>{{ word.count }}</td>
              <td>{{ word.people }}</td>
              <td>
                <span v-if="word.isNew" class="lc-chip lc-chip--accent">{{
                  t('spaces.analytics.learning.class.words.newMark')
                }}</span>
                <span v-else-if="word.rising" class="lc-chip lc-chip--danger">{{
                  t('spaces.analytics.learning.class.words.rising')
                }}</span>
              </td>
            </tr>
          </tbody>
        </table>
      </section>
    </div>
    <section class="an-card">
      <h3 class="an-card__title">{{ t('spaces.analytics.learning.class.words.errorsTitle') }}</h3>
      <p class="an-note">{{ t('spaces.analytics.learning.class.words.errorsHint') }}</p>
      <p v-if="!data.errors.length" class="an-note lc-gap">
        {{ t('spaces.analytics.learning.class.words.errorsEmpty') }}
      </p>
      <table v-else class="lc-table lc-gap">
        <thead>
          <tr>
            <th>{{ t('spaces.analytics.learning.class.words.errCol.line') }}</th>
            <th>{{ t('spaces.analytics.learning.class.words.errCol.count') }}</th>
            <th>{{ t('spaces.analytics.learning.class.words.errCol.people') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="error in data.errors" :key="error.line">
            <td class="lc-code">{{ error.line }}</td>
            <td>{{ error.count }}</td>
            <td>{{ error.people }}</td>
          </tr>
        </tbody>
      </table>
    </section>
  </div>
</template>

<script setup lang="ts">
// 高频词：词云、排行（带「新」与「变多」）、高频报错。说过的人不足 3 个的词服务端就不给。
import type { LearningClassWords } from '@/types/apiSpaces'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps<{ data: LearningClassWords }>()
const { t } = useI18n()

const most = computed(() => Math.max(1, ...props.data.words.map((word) => word.count)))
const size = (count: number) => 13 + Math.round((count / most.value) * 15)
</script>

<style scoped src="../analytics.css"></style>
<style scoped src="./learningClass.css"></style>

<style scoped>
.lc-cloud {
  padding: 8px 0;
  text-align: center;
}

.lc-cloud__word {
  display: inline-block;
  margin: 4px 10px;
  font-weight: 600;
  line-height: var(--lh-18);
}

.lc-cloud__word--new {
  padding: 0 6px;
  border-radius: var(--radius-sm);
  background: var(--accent-wash);
  color: var(--accent-ink);
}
</style>
