<script setup lang="ts">
// 「文档」那一格的接线外壳。
//
// 面板里那一半（`components/panels/PanelDoc.vue`）只吃 props：场景棘轮认的「场景」是
// `components/panels/**` 下每个 SFC，A 档的意思是「给一组 props 就能单独出画面」，取数
// 一滴都不能漏进去。所以打开文档、读评论串、把名册读成名字这三件取数的活留在这里，
// 结果（三包状态和动作）原样递下去。
//
// 外壳只能待在这儿：`components/panels/**` 底下每个 SFC 都是场景，包括外壳自己，所以它
// 得站在场景之外；`components/**` 又不许直接连接口层（`pnpm run lint:boundary`），
// 所以取数走 `composables/`。同一条理由见 `components/routine/RoutinePanelHost.vue`。
//
// 调用点：工作面板自由区的资料库文档页签（一个页签一只，所以外壳按页签渲染）、项目
// 文档页的章程（项目总览）、任务概览里的实况文档、「综合」概览里的项目总览。
import type { PanelDocument } from '../../composables/usePanelDoc'
import type { ProjectMemberRow, Topic } from '../../cx_types'
import type { DocReviewRequest } from '../../lib/docReview'

import { ref } from 'vue'

import { useDocPeople } from '../../composables/useDocPeople'
import { useDocThreads } from '../../composables/useDocThreads'
import { usePanelDoc } from '../../composables/usePanelDoc'
import PanelDoc from '../panels/PanelDoc.vue'

import { t } from '@/i18n'

const props = withDefaults(
  defineProps<{
    topic: Topic | null
    /** 打开的是这个房间里某个任务的实况文档。 */
    taskId?: string | null
    /** 项目资料库里的一份文档：直接打开它。 */
    document?: PanelDocument | null
    /** 父层在 AI 动过之后加一。 */
    activityTick: number
    /** 项目话题表：正文里那些 `<#id>` chip 靠它认名字。 */
    topicList?: Topic[]
    agentName?: string
    agentHandle?: string | null
    /** 项目名册：正文里 @ 得到的人。 */
    members?: ProjectMemberRow[]
    /** 画在一整页里（资料库的章程），见 PanelDoc。 */
    bare?: boolean
    /** 顶栏画到页面上的哪个位置，见 PanelDoc。 */
    barTo?: string
    /** 跟着外面那一列一起滚，见 PanelDocView。 */
    flow?: boolean
  }>(),
  {
    taskId: null,
    document: null,
    topicList: () => [],
    agentName: () => t('work.room.defaultAgentName'),
    agentHandle: null,
    members: () => [],
    bare: false,
    barTo: undefined,
    flow: false,
  }
)

const emit = defineEmits<{
  (e: 'open-topic', topicId: string): void
  (e: 'mention-click', handle: string): void
  (e: 'open-file', path: string): void
  (e: 'titled', title: string): void
  (e: 'delete'): void
}>()

const docRef = ref<InstanceType<typeof PanelDoc> | null>(null)

const docPanel = usePanelDoc(props)
const docThreads = useDocThreads(() => docPanel.documentId.value)
const docPeople = useDocPeople({
  members: () => props.members,
  agentHandle: () => props.agentHandle,
  agentName: () => props.agentName,
})

// 面板那半边的外部接口原样透出去 —— 拿着外壳的 ref 调它们（资料库文档卡上的「查看
// 改动」、高亮某一轮、闪一下），中间多一层不该让这些线断掉。
defineExpose({
  pulse: () => docRef.value?.pulse(),
  highlightTurn: (turnId: string) => docRef.value?.highlightTurn(turnId),
  reviewEdits: (request: DocReviewRequest) => docRef.value?.reviewEdits(request),
})
</script>

<template>
  <PanelDoc
    ref="docRef"
    :doc-panel="docPanel"
    :doc-threads="docThreads"
    :doc-people="docPeople"
    :topic="props.topic"
    :task-id="props.taskId"
    :document="props.document"
    :activity-tick="props.activityTick"
    :topic-list="props.topicList"
    :agent-name="props.agentName"
    :agent-handle="props.agentHandle"
    :bare="props.bare"
    :bar-to="props.barTo"
    :flow="props.flow"
    @open-topic="emit('open-topic', $event)"
    @mention-click="emit('mention-click', $event)"
    @open-file="emit('open-file', $event)"
    @titled="emit('titled', $event)"
    @delete="emit('delete')"
  >
    <template #lead><slot name="lead" /></template>
  </PanelDoc>
</template>
