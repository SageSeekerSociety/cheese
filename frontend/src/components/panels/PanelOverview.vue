<script setup lang="ts">
// 总览 —— 一个房间的右半边，从上到下：Task Progress，然后是房间文档。
//
// 在它之前这是两个平级 tab（文档 / 任务）。合成一个不是为了少一格：它们回答的是
// 同一个问题的两半——「这个房间在干什么」——而分成两格意味着看完一半得先想起来
// 还有另一半，于是大多数人只看文档，房间里有几条活在跑就没人知道。
//
// 点开看板上的一张卡就在这一格里往下钻一层：整段换成那张卡（`PanelCard`），左上角
// 一个「看板」退回来。地址里的 `?card=` 说的就是这一层，所以它是一条能发给别人的
// 链接 —— 而不是「跳到一个新地点」：一件活不是地点。
import type { Topic } from '../../cx_types'

import { ref } from 'vue'

import PanelCard from './PanelCard.vue'
import PanelDoc from './PanelDoc.vue'
import TaskProgress from './TaskProgress.vue'

const props = withDefaults(
  defineProps<{
    topic: Topic | null
    activityTick: number
    topicList?: Topic[]
    active?: boolean
    refreshTick?: number
    /** 地址里的 `?card=` —— 非空就是在看这一张卡，而不是看板加文档。 */
    openCardId?: string | null
  }>(),
  { topicList: () => [], active: false, refreshTick: 0, openCardId: null }
)

const emit = defineEmits<{
  (e: 'open-topic', topicId: string): void
  (e: 'open-card', taskId: string | null): void
  /** 卡上的「去验收」，一路透到 `TopicView`（那里才知道面板开在哪一格）。 */
  (e: 'review'): void
  (e: 'mention-click', handle: string): void
  (e: 'open-file', path: string): void
}>()

const docRef = ref<InstanceType<typeof PanelDoc> | null>(null)

// 文档那一半的外部接口原样透出去 —— WorkPanel 拿着 ref 调它们（<&path> 芯片、
// 高亮某一轮），合并 tab 不该让这些线断掉。
defineExpose({
  pulse: () => docRef.value?.pulse(),
  highlightTurn: (turnId: string) => docRef.value?.highlightTurn(turnId),
})
</script>

<template>
  <div class="panel-overview">
    <PanelCard
      v-if="props.openCardId"
      :room-id="props.topic?.id ?? null"
      :card-id="props.openCardId"
      :active="props.active"
      :refresh-tick="props.refreshTick"
      @back="emit('open-card', null)"
      @review="emit('review')"
    />
    <template v-else>
      <TaskProgress
        :topic="props.topic"
        :active="props.active"
        :refresh-tick="props.refreshTick"
        @open-card="emit('open-card', $event)"
      />
      <PanelDoc
        ref="docRef"
        class="panel-overview__doc"
        :topic="props.topic"
        :activity-tick="props.activityTick"
        :topic-list="props.topicList"
        @open-topic="emit('open-topic', $event)"
        @mention-click="emit('mention-click', $event)"
        @open-file="emit('open-file', $event)"
      />
    </template>
  </div>
</template>

<style scoped>
/* min-width: 0 next to the min-height: 0 — a flex item refuses to shrink below
   its content's min-content size, and that applies to width as much as height.
   The doc below carries markdown tables, whose min-content width is however wide
   the widest cell insists on being; without this the whole doc column is sized
   to the table and hangs off the right edge of the panel, clipped rather than
   scrollable. The table wrapper already scrolls horizontally on its own — it
   only gets the chance once the column above it is allowed to shrink. */
.panel-overview {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-height: 0;
  min-width: 0;
}
.panel-overview__doc {
  flex: 1 1 auto;
  min-height: 0;
}
</style>
