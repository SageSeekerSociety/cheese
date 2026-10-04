<script setup lang="ts">
// 总览 —— 一个房间的右半边，从上到下：看板、上一轮的进度清单、房间文档，最底下
// 一行收起着的「这个房间里的东西」。
//
// 在它之前这是两个平级 tab（文档 / 任务）。合成一个不是为了少一格：它们回答的是
// 同一个问题的两半——「这个房间在干什么」——而分成两格意味着看完一半得先想起来
// 还有另一半，于是大多数人只看文档，房间里有几条活在跑就没人知道。
//
// 点开看板上的一张卡就在这一格里往下钻一层：整段换成那张卡（`PanelCard`），左上角
// 一个「看板」退回来。地址里的 `?card=` 说的就是这一层，所以它是一条能发给别人的
// 链接 —— 而不是「跳到一个新地点」：一件活不是地点。
import type { Topic } from '../../cx_types'
import type { DocReviewRequest } from '../../lib/docReview'

import { defineAsyncComponent, ref, watch } from 'vue'

import RoomOutputs from './preview/RoomOutputs.vue'
import PanelCard from './PanelCard.vue'
import PanelProgress from './PanelProgress.vue'
import TaskProgress from './TaskProgress.vue'

import { t } from '@/i18n'

// 文档那一格带着整个编辑器（tiptap + ProseMirror + 代码高亮，约 250 KB gzip），
// 静态引入的话打开一个房间要先把它下完、解析完，左边的消息才画得出来。它在这一列
// 的最底下，晚一点到不会把别的东西往下推。
const PanelDoc = defineAsyncComponent(() => import('./PanelDoc.vue'))

const props = withDefaults(
  defineProps<{
    topic: Topic | null
    activityTick: number
    topicList?: Topic[]
    active?: boolean
    refreshTick?: number
    /** 地址里的 `?card=` —— 非空就是在看这一张卡，而不是看板加文档。 */
    openCardId?: string | null
    /** 开着的那张卡打开时停在哪一条。 */
    cardFocusBlock?: string | null
    /** 项目 AI 队友的名字，传给文档那一格。 */
    agentName?: string
    /** 项目 AI 队友的 handle，传给文档那一格。 */
    agentHandle?: string | null
    /** handle → 名字，给钻进去的那张卡换点名和说话人。 */
    memberNames?: Record<string, string>
  }>(),
  {
    topicList: () => [],
    active: false,
    refreshTick: 0,
    openCardId: null,
    cardFocusBlock: null,
    agentName: () => t('work.room.defaultAgentName'),
    agentHandle: null,
    memberNames: () => ({}),
  }
)

const emit = defineEmits<{
  (e: 'open-topic', topicId: string): void
  (e: 'open-card', taskId: string | null): void
  /** 卡上的「去验收」，一路透到 `TopicView`（那里才知道面板开在哪一格）。 */
  (e: 'review'): void
  (e: 'mention-click', handle: string): void
  (e: 'open-file', path: string): void
  /** 「这个房间里的东西」里点开了一份：开成自由区的一个页签。 */
  (e: 'open-output', path: string): void
}>()

// 一轮结束时房间里可能多摆了一样东西；这一块一直挂着，所以跟着那一下重读。
const outputsRef = ref<{ reload: () => Promise<void> } | null>(null)
watch(
  () => props.refreshTick,
  () => void outputsRef.value?.reload()
)

const docRef = ref<{
  pulse: () => void
  highlightTurn: (turnId: string) => void
  reviewEdits: (request: DocReviewRequest) => void
} | null>(null)

// 「查看改动」：文档那一半可能还没摆出来（开着一张卡、或者组件还在加载），先记着，摆出来
// 就交给它。
const pendingReview = ref<DocReviewRequest | null>(null)
function reviewEdits(request: DocReviewRequest) {
  if (docRef.value) return docRef.value.reviewEdits(request)
  pendingReview.value = request
  if (props.openCardId) emit('open-card', null)
}
watch(docRef, (doc) => {
  if (!doc || !pendingReview.value) return
  doc.reviewEdits(pendingReview.value)
  pendingReview.value = null
})

// 文档那一半的外部接口原样透出去 —— WorkPanel 拿着 ref 调它们（<&path> 芯片、
// 高亮某一轮、查看改动），合并 tab 不该让这些线断掉。
defineExpose({
  pulse: () => docRef.value?.pulse(),
  highlightTurn: (turnId: string) => docRef.value?.highlightTurn(turnId),
  reviewEdits,
})
</script>

<template>
  <div class="panel-overview">
    <!-- 钻进一张卡 / 退回看板是同一处的一层往下、一层往上：进去的那一层从右边进，
         退回来的从左边进，0.2s。走掉的那一层不演，直接让位。 -->
    <Transition :name="props.openCardId ? 'drill-in' : 'drill-out'">
      <PanelCard
        v-if="props.openCardId"
        key="card"
        :room-id="props.topic?.id ?? null"
        :card-id="props.openCardId"
        :focus-block="props.cardFocusBlock"
        :active="props.active"
        :refresh-tick="props.refreshTick"
        :member-names="props.memberNames"
        :agent-name="props.agentName"
        @back="emit('open-card', null)"
        @review="emit('review')"
      />
      <div v-else key="board" class="panel-overview__board">
        <TaskProgress
          :topic="props.topic"
          :active="props.active"
          :refresh-tick="props.refreshTick"
          @open-card="emit('open-card', $event)"
        />
        <PanelProgress :topic="props.topic" :refresh-tick="props.refreshTick" />
        <PanelDoc
          ref="docRef"
          :agent-name="props.agentName"
          :agent-handle="props.agentHandle"
          class="panel-overview__doc"
          :topic="props.topic"
          :activity-tick="props.activityTick"
          :topic-list="props.topicList"
          @open-topic="emit('open-topic', $event)"
          @mention-click="emit('mention-click', $event)"
          @open-file="emit('open-file', $event)"
        />
        <RoomOutputs ref="outputsRef" :topic-id="props.topic?.id ?? null" @open="emit('open-output', $event)" />
      </div>
    </Transition>
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
/* 看板 + 文档那一层。它以前是一个 <template>，现在要当 Transition 的那一个子元素，
   所以得是个盒子——盒子自己照原来的纵向排法摆它的两个孩子。 */
.panel-overview__board {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-height: 0;
  min-width: 0;
}
.drill-in-enter-active,
.drill-out-enter-active {
  transition:
    transform 0.2s ease,
    opacity 0.2s ease;
}
.drill-in-enter-from {
  transform: translateX(16px);
  opacity: 0;
}
.drill-out-enter-from {
  transform: translateX(-16px);
  opacity: 0;
}
.panel-overview__doc {
  flex: 1 1 auto;
  min-height: 0;
}
</style>
