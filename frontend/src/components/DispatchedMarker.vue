<script setup lang="ts">
/**
 * 「这件事已经派出去了」—— 父话题时间线上的一条派生行 (issue #314)。
 *
 * 不是一条消息，库里没有它：位置和内容全部由子话题的 parent_id + created_at 算出
 * 来（见 lib/splitMarkers.ts）。存在的理由是 8-11 那次事故——父话题看不出第 N 项
 * 已经归子话题了，于是照自己那份清单又做了一遍，两套不兼容的迁移各自跑绿。
 *
 * 视觉上刻意做成一条**分隔线**而不是气泡：它标的是时间线上的一个转折点（往下走
 * 的这段时间里，这件事在别处做），不是谁说了一句话。
 */
import type { SplitMarker } from '../lib/splitMarkers'

import { computed } from 'vue'

import TimelineMark from './TimelineMark.vue'

const props = defineProps<{ marker: SplitMarker }>()
const emit = defineEmits<{ (e: 'open', topicId: string): void }>()

// 归档 = 那件事在子话题里完事了。同一行改口而不是换一种标记：读的人关心的是「这段
// 归谁」，而不是子话题的生命周期。
const note = computed(() => (props.marker.status === 'archived' ? '这部分已在该话题完成' : '这部分在该话题进行'))

function open() {
  emit('open', props.marker.topicId)
}
</script>

<template>
  <TimelineMark>
    <span class="dispatched" data-testid="dispatched-marker" :data-topic-id="marker.topicId">
      <v-icon size="13" class="dispatched__icon">mdi-call-split</v-icon>
      已拆出子话题<button type="button" class="dispatched__link" @click="open">《{{ marker.title }}》</button> ·
      {{ note }}
    </span>
  </TimelineMark>
</template>

<style scoped>
/* 形状归 TimelineMark（时间刻度那一种原型）；这里只剩这条标记自己的东西。 */
.dispatched {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
}
.dispatched__icon {
  flex: none;
  color: var(--faint);
}
.dispatched__link {
  color: var(--accent-ink);
  cursor: pointer;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.dispatched__link:hover {
  text-decoration: underline;
}
</style>
