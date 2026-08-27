<script setup lang="ts">
// 总览 —— 一个房间的右半边，从上到下：Task Progress，然后是房间文档。
//
// 在它之前这是两个平级 tab（文档 / 任务）。合成一个不是为了少一格：它们回答的是
// 同一个问题的两半——「这个房间在干什么」——而分成两格意味着看完一半得先想起来
// 还有另一半，于是大多数人只看文档，房间里有几条活在跑就没人知道。
//
// 打开一条支线时，上面那段列的仍然是**它所在房间**的全部活（`TaskProgress` 自己
// 问的就是房间），下面的文档换成这条支线自己的——「右边总览只显示这条 task 自己
// 的文档」。文档由 `PanelDoc` 按地点 id 取，所以这里不用做什么。
import type { Topic } from '../../cx_types'

import { ref } from 'vue'

import PanelDoc from './PanelDoc.vue'
import TaskProgress from './TaskProgress.vue'

const props = withDefaults(
  defineProps<{
    topic: Topic | null
    activityTick: number
    topicList?: Topic[]
    active?: boolean
    refreshTick?: number
  }>(),
  { topicList: () => [], active: false, refreshTick: 0 }
)

const emit = defineEmits<{
  (e: 'open-topic', topicId: string): void
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
    <TaskProgress
      :topic="props.topic"
      :active="props.active"
      :refresh-tick="props.refreshTick"
      @open-topic="emit('open-topic', $event)"
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
  </div>
</template>

<style scoped>
.panel-overview {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-height: 0;
}
.panel-overview__doc {
  flex: 1 1 auto;
  min-height: 0;
}
</style>
