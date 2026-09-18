<script setup lang="ts">
import type { FeedbackPriority, FeedbackStatus } from '@/lib/feedbackMock'

import { computed } from 'vue'

import { PRIORITY_META, STATUS_META } from '@/lib/feedbackMock'

// 状态/优先级的那个小药丸。四种状态色分别在卡片、详情页右侧、管理员列表里出现，
// 所以颜色只能有一份来源（lib/feedbackMock 的 META），否则「评估中」在列表里是黄
// 的、在详情里是灰的，读的人只会得出「界面在骗我」。
//
// 底色 + 文字色成对取自同一组 token（wash / ink）。**不要**只写底色不写文字色：
// wash 是浅到只能放 ink 的，直接压 --text 上去在浅色主题里是够的、在深色主题里
// 会糊成一片。
const props = defineProps<{
  status?: FeedbackStatus
  priority?: FeedbackPriority
}>()

const meta = computed(() =>
  props.priority ? PRIORITY_META[props.priority] : props.status ? STATUS_META[props.status] : null
)
</script>

<template>
  <span v-if="meta" class="fb-chip" :style="{ background: meta.wash, color: meta.ink }">
    <span class="status-dot" :style="{ background: meta.dot }" aria-hidden="true" />
    {{ meta.label }}
  </span>
</template>

<style scoped>
.fb-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 2px 9px;
  border-radius: var(--radius-pill);
  font-size: 12px;
  line-height: 1.7;
  white-space: nowrap;
}
</style>
