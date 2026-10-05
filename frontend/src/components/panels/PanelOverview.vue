<script setup lang="ts">
// 总览 —— 一个房间的右半边，从上到下：看板、上一轮的进度清单、房间文档，最底下
// 一行收起着的「这个房间里的东西」。
//
// 在它之前这是两个平级 tab（文档 / 任务）。合成一个不是为了少一格：它们回答的是
// 同一个问题的两半——「这个房间在干什么」——而分成两格意味着看完一半得先想起来
// 还有另一半，于是大多数人只看文档，房间里有几条活在跑就没人知道。
//
// 点开看板上的一个任务就去那个任务自己的页面：任务有自己的对话和实况文档。
import type { DocPeopleBundle } from '../../composables/useDocPeople'
import type { DocThreadsBundle } from '../../composables/useDocThreads'
import type { PanelDocBundle } from '../../composables/usePanelDoc'
import type { Block, RoomTask, TodoItem, Topic } from '../../cx_types'
import type { DocReviewRequest } from '../../lib/docReview'
import type { DocumentTemplate, RoomOutput } from '../../types/roomOutput'

import { defineAsyncComponent, ref, watch } from 'vue'

import RoomOutputs from './preview/RoomOutputs.vue'
import PanelProgress from './PanelProgress.vue'
import TaskProgress from './TaskProgress.vue'

// 文档那一格带着整个编辑器（tiptap + ProseMirror + 代码高亮，约 250 KB gzip），
// 静态引入的话打开一个房间要先把它下完、解析完，左边的消息才画得出来。它在这一列
// 的最底下，晚一点到不会把别的东西往下推。
const PanelDoc = defineAsyncComponent(() => import('./PanelDoc.vue'))

// 这一层只决定「四块从上到下怎么摆」：数由 `components/work/PanelOverviewHost.vue`
// 取（它渲染这一格），所以下面每一块都只吃 props。
const props = withDefaults(
  defineProps<{
    topic: Topic | null
    activityTick: number
    topicList?: Topic[]
    /** 文档那一格的取数（`composables/usePanelDoc.ts` 那一包），原样传给文档那一格。 */
    docPanel: PanelDocBundle
    /** 评论串那一包。 */
    docThreads: DocThreadsBundle
    /** 名册读成名字那一包。 */
    docPeople: DocPeopleBundle
    /** 上一轮芝士留下的清单。 */
    progressItems?: TodoItem[]
    /** 这个房间派出去的活，每条带最新那一块。 */
    boardRows?: (RoomTask & { blocks?: Block[] })[]
    boardLoading?: boolean
    boardError?: string | null
    /** 「这个房间里的东西」。 */
    outputs?: RoomOutput[]
    outputTemplates?: DocumentTemplate[]
    loadOutputTemplates?: () => void
    saveOutputToLibrary?: (path: string) => Promise<string>
    createOutputFromTemplate?: (templateId: string, path: string) => Promise<void>
  }>(),
  {
    topicList: () => [],
    progressItems: () => [],
    boardRows: () => [],
    boardLoading: false,
    boardError: null,
    outputs: () => [],
    outputTemplates: () => [],
    loadOutputTemplates: undefined,
    saveOutputToLibrary: undefined,
    createOutputFromTemplate: undefined,
  }
)

const emit = defineEmits<{
  (e: 'open-topic', topicId: string): void
  (e: 'open-card', taskId: string): void
  (e: 'mention-click', handle: string): void
  (e: 'open-file', path: string): void
  /** 「这个房间里的东西」里点开了一份：开成自由区的一个页签。 */
  (e: 'open-output', path: string): void
}>()

const docRef = ref<{
  pulse: () => void
  highlightTurn: (turnId: string) => void
  reviewEdits: (request: DocReviewRequest) => void
} | null>(null)

// 「查看改动」：文档那一半可能还没摆出来（组件还在加载），先记着，摆出来就交给它。
const pendingReview = ref<DocReviewRequest | null>(null)
function reviewEdits(request: DocReviewRequest) {
  if (docRef.value) return docRef.value.reviewEdits(request)
  pendingReview.value = request
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
    <div class="panel-overview__board">
      <TaskProgress
        :rows="props.boardRows"
        :loading="props.boardLoading"
        :error-msg="props.boardError"
        @open-card="emit('open-card', $event)"
      />
      <PanelProgress :items="props.progressItems" />
      <PanelDoc
        ref="docRef"
        :doc-panel="props.docPanel"
        :doc-threads="props.docThreads"
        :doc-people="props.docPeople"
        class="panel-overview__doc"
        :topic="props.topic"
        :activity-tick="props.activityTick"
        :topic-list="props.topicList"
        @open-topic="emit('open-topic', $event)"
        @mention-click="emit('mention-click', $event)"
        @open-file="emit('open-file', $event)"
      />
      <RoomOutputs
        :topic-id="props.topic?.id ?? null"
        :outputs="props.outputs"
        :templates="props.outputTemplates"
        :load-templates="props.loadOutputTemplates"
        :save-to-library="props.saveOutputToLibrary"
        :create-from-template="props.createOutputFromTemplate"
        @open="emit('open-output', $event)"
      />
    </div>
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
/* 看板 + 文档那一层，照纵向排法摆它的几个孩子。 */
.panel-overview__board {
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
