<template>
  <v-card flat rounded="lg" class="stuck-card">
    <div class="stuck-card__head">
      <v-checkbox
        v-model="checked"
        class="stuck-card__check"
        density="compact"
        hide-details
        :aria-label="`勾选卡点 ${label}`"
      />

      <div class="stuck-card__titles">
        <div class="stuck-card__title">{{ label }}</div>
        <div class="stuck-card__counts">{{ counts }}</div>
      </div>

      <div class="stuck-card__latest">最近 {{ formatLearningTime(point.latestAt) }}</div>
    </div>

    <LearningQuoteItem :excerpt="point.example" />
  </v-card>
</template>

<script setup lang="ts">
import type { SpaceLearningStuckPoint } from '@/network/api/spaces/types'

import { computed } from 'vue'

import { formatLearningTime, knowledgePointLabel } from '../helpers'

import LearningQuoteItem from './LearningQuoteItem.vue'

const props = defineProps<{
  point: SpaceLearningStuckPoint
}>()

// 勾中一个卡点就把它的那条示例发言带进提纲 —— 老师勾的是「这个知识点要讲」，
// 而能带走的东西只有后端给的那一条例子。
const checked = defineModel<boolean>('checked', { default: false })

const label = computed(() => knowledgePointLabel(props.point.knowledgePoint))

const counts = computed(
  () => `${props.point.studentCount} 个学生 · ${props.point.projectCount} 个项目 · ${props.point.questionCount} 条发言`
)
</script>

<style scoped lang="scss">
.stuck-card {
  padding: 16px;
  background-color: rgba(var(--v-theme-surface), 1);
  border: 1px solid rgba(var(--v-theme-on-surface), 0.08);
}

.stuck-card__head {
  display: flex;
  gap: 8px;
  align-items: flex-start;
}

.stuck-card__check {
  flex: 0 0 auto;
  margin: 0;
}

.stuck-card__titles {
  flex: 1 1 auto;
  min-width: 0;
}

.stuck-card__title {
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15);
  color: var(--ink);
  overflow-wrap: anywhere;
}

.stuck-card__counts {
  margin-top: 4px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.stuck-card__latest {
  flex: 0 0 auto;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--faint);
}
</style>
