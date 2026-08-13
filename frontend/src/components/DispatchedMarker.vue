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

const props = defineProps<{ marker: SplitMarker }>()
const emit = defineEmits<{ (e: 'open', topicId: string): void }>()

// 归档 = 那件事在子话题里完事了。同一行改口而不是换一种标记：读的人关心的是「这段
// 归谁」，而不是子话题的生命周期。
const note = computed(() => (props.marker.status === 'archived' ? '这件事在那边做完了' : '这件事在那边做，不在这里'))

function open() {
  emit('open', props.marker.topicId)
}
</script>

<template>
  <div class="dispatched" data-testid="dispatched-marker" :data-topic-id="marker.topicId">
    <span class="dispatched__rule" aria-hidden="true" />
    <span class="dispatched__body">
      <v-icon size="13" class="dispatched__icon">mdi-call-split</v-icon>
      <span class="dispatched__text"
        >已派出<button type="button" class="dispatched__link" @click="open">《{{ marker.title }}》</button>——
        {{ note }}</span
      >
    </span>
    <span class="dispatched__rule" aria-hidden="true" />
  </div>
</template>

<style scoped>
.dispatched {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 10px 16px;
  font-size: 12px;
  color: var(--muted);
}
.dispatched__rule {
  flex: 1;
  height: 1px;
  background: var(--line);
}
.dispatched__body {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  max-width: 78%;
}
.dispatched__icon {
  color: var(--faint);
  flex: none;
}
.dispatched__text {
  overflow-wrap: anywhere;
}
.dispatched__link {
  border: none;
  background: none;
  padding: 0;
  font: inherit;
  color: rgb(var(--v-theme-primary));
  cursor: pointer;
}
.dispatched__link:hover {
  text-decoration: underline;
}
</style>
