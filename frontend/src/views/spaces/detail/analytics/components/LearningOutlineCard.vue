<template>
  <v-card flat rounded="lg" class="outline-card">
    <div v-for="section in outline.sections" :key="section.session" class="outline-session">
      <div class="outline-session__head">
        <span class="outline-session__session">建议第 {{ section.session }} 讲</span>
        <span class="outline-session__point">{{ section.knowledgePoint }}</span>
      </div>

      <LearningQuoteItem v-for="excerpt in section.excerpts" :key="excerpt.blockId" :excerpt="excerpt" />
    </div>

    <p v-if="!outline.sections.length" class="outline-card__empty">暂无可讲解的内容</p>

    <p v-if="outline.missing.length" class="outline-card__missing">
      有 {{ outline.missing.length }} 条发言已经读不到，没有放进这份提纲
    </p>
  </v-card>
</template>

<script setup lang="ts">
import type { SpaceLearningOutline } from '@/network/api/spaces/types'

import LearningQuoteItem from './LearningQuoteItem.vue'

defineProps<{
  outline: SpaceLearningOutline
}>()
</script>

<style scoped lang="scss">
.outline-card {
  padding: 8px 16px 16px;
  background-color: rgba(var(--v-theme-surface), 1);
  border: 1px solid rgba(var(--v-theme-on-surface), 0.08);
}

.outline-session {
  padding-top: 16px;
}

.outline-session__head {
  display: flex;
  gap: 8px;
  align-items: center;
  padding-bottom: 4px;
  border-bottom: 1px solid var(--line-2);
}

.outline-session__session {
  font-size: 12px;
  font-weight: 600;
  line-height: var(--lh-12);
  letter-spacing: 0.04em;
  color: var(--faint);
}

.outline-session__point {
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15);
  color: var(--ink);
  overflow-wrap: anywhere;
}

.outline-card__empty {
  margin: 16px 0 0;
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--muted);
}

.outline-card__missing {
  margin: 16px 0 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}
</style>
