<script setup lang="ts">
/**
 * 「这件事已经派出去了」—— 房间时间线上的一条派生行 (issue #314)。
 *
 * 不是一条消息，库里没有它：位置和内容全部由这条支线的 room_id + created_at 算出
 * 来（见 lib/splitMarkers.ts）。存在的理由是 8-11 那次事故——房间看不出第 N 项
 * 已经归某条支线了，于是照自己那份清单又做了一遍，两套不兼容的迁移各自跑绿。
 *
 * 它是房间里发生的一件事，不是谁说的话，所以和「谁加入了话题」长成同一种：居中
 * 一行淡字，不进头像列。日期线和新消息线那种带横线的刻度留给时间本身。
 */
import type { SplitMarker } from '../lib/splitMarkers'

import { computed } from 'vue'

const props = defineProps<{ marker: SplitMarker }>()
const emit = defineEmits<{ (e: 'open', taskId: string): void }>()

// closed = 那件事做完了。同一行改口而不是换一种标记：读的人关心的是「这段归谁」，
// 而不是这条支线的生命周期。
const note = computed(() => (props.marker.status === 'closed' ? '已完成' : '进行中'))

function open() {
  emit('open', props.marker.taskId)
}
</script>

<template>
  <div class="dispatched" data-testid="dispatched-marker" :data-task-id="marker.taskId">
    <v-icon size="13" class="dispatched__icon">mdi-call-split</v-icon>
    <span class="dispatched__text">
      新建任务<button type="button" class="dispatched__link" @click="open">《{{ marker.title }}》</button> ·
      {{ note }}
    </span>
  </div>
</template>

<style scoped>
/* 和 RoomNotice 的 .room-happening 同一档：12px、--faint、居中。 */
.dispatched {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  margin: 10px 16px;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--faint);
}
.dispatched__text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.dispatched__icon {
  flex: none;
  color: var(--faint);
}
.dispatched__link {
  color: var(--accent-ink);
  cursor: pointer;
}
.dispatched__link:hover {
  text-decoration: underline;
}
</style>
