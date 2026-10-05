<script setup lang="ts">
// 房间「总览」那一格的接线外壳。
//
// 总览从上到下是四块：看板、上一轮的进度清单、房间文档、最底下收起着的「这个房间里的
// 东西」。它们画的是同一个问题（「这个房间在干什么」），取的数也来自同一批接口，所以
// 取数集中在 `composables/usePanelOverview.ts` 里，这一只负责接上去：传下去数、把事件
// 转出去、把文档那一半的 ref 透出去。`components/panels/**` 下每个 SFC 都是场景棘轮里的
// 「场景」，场景只吃 props 和事件，取数一滴都不能漏进 `PanelOverview.vue` 或它下面。
//
// 外壳只能待在这儿：`components/panels/**` 底下每个 SFC 都是场景、包括外壳自己，所以
// 它得站在场景之外。同一条理由见 `components/routine/RoutinePanelHost.vue`。
import type { ProjectMemberRow, Topic } from '../../cx_types'
import type { DocReviewRequest } from '../../lib/docReview'

import { ref } from 'vue'

import { usePanelOverview } from '../../composables/usePanelOverview'
import PanelOverview from '../panels/PanelOverview.vue'

import { t } from '@/i18n'

const props = withDefaults(
  defineProps<{
    topic: Topic | null
    activityTick?: number
    topicList?: Topic[]
    /** 总览这一格在屏幕上。看不到的时候不去拉看板。 */
    active?: boolean
    /** 每有一轮动静就加一。 */
    refreshTick?: number
    /** 项目 AI 队友的名字，传给文档那一格。 */
    agentName?: string
    /** 项目 AI 队友的 handle，传给文档那一格。 */
    agentHandle?: string | null
    /** 项目名册，传给文档那一格。 */
    members?: ProjectMemberRow[]
  }>(),
  {
    activityTick: 0,
    topicList: () => [],
    active: false,
    refreshTick: 0,
    agentName: () => t('work.room.defaultAgentName'),
    agentHandle: null,
    members: () => [],
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

const {
  boardRows,
  boardLoading,
  boardError,
  progressItems,
  outputItems,
  outputTemplates,
  loadOutputTemplates,
  saveOutputToLibrary,
  createOutputFromTemplate,
} = usePanelOverview(props)

/** 建好的那一份就地开成页签——建和开是两件事，后者是这一层的事。 */
async function createOutput(templateId: string, path: string): Promise<void> {
  const made = await createOutputFromTemplate(templateId, path)
  if (made) emit('open-output', made)
}

// 文档那一半的外部接口原样透出去 —— WorkPanel 拿着外壳的 ref 调它们（<&path> 芯片、
// 高亮某一轮、查看改动），中间多一层不该让这些线断掉。
const overviewRef = ref<InstanceType<typeof PanelOverview> | null>(null)

defineExpose({
  pulse: () => overviewRef.value?.pulse(),
  highlightTurn: (turnId: string) => overviewRef.value?.highlightTurn(turnId),
  reviewEdits: (request: DocReviewRequest) => overviewRef.value?.reviewEdits(request),
})
</script>

<template>
  <PanelOverview
    ref="overviewRef"
    :agent-name="props.agentName"
    :agent-handle="props.agentHandle"
    :members="props.members"
    :topic="props.topic"
    :activity-tick="props.activityTick"
    :topic-list="props.topicList"
    :progress-items="progressItems"
    :board-rows="boardRows"
    :board-loading="boardLoading"
    :board-error="boardError"
    :outputs="outputItems"
    :output-templates="outputTemplates"
    :load-output-templates="loadOutputTemplates"
    :save-output-to-library="saveOutputToLibrary"
    :create-output-from-template="createOutput"
    @open-topic="emit('open-topic', $event)"
    @open-card="emit('open-card', $event)"
    @mention-click="emit('mention-click', $event)"
    @open-file="emit('open-file', $event)"
    @open-output="emit('open-output', $event)"
  />
</template>
