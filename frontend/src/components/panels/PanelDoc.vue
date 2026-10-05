<script setup lang="ts">
// 话题页右侧「文档」这一格 —— 一只薄容器。
//
// 它以前是一个 2439 行的组件：自己 import 五个接口函数、自己每 2.5 秒把正文写回去、自己
// 管着服务端版本号与冲突，然后又自己把这一切画出来。于是「文档」这个界面在测试和 /demo
// 里都得先立一个假后端，而任何一行样式调整都要在一个两千行的文件里找。
//
// 现在两边分家，和 #2130 的「改动」、#2158 的「预览」是同一个形状：
//   - 取数（打开协同文档、已存的那一版、节点）→ `composables/usePanelDoc.ts`；
//     评论串（读、回复、解决，跟着房间的信号刷新）→ `composables/useDocThreads.ts`
//   - 画（横条上写哪句话、一栏正文、评论区）
//     → `components/panels/PanelDocView.vue`，只凭 props 渲染
//   - 编辑器本身（tiptap 实例、几种装饰、别人的光标、段落闪一下）
//     → `components/panels/doc/DocSurface.vue`，画不动的一层放在那儿
// 取数的那几个组合式函数由上一层的接线外壳调用（`components/work/PanelDocHost.vue`，
// 房间总览那一格是 `components/work/PanelOverviewHost.vue`）：场景棘轮里面板自己必须只吃
// props，取数一滴都不能漏进 `components/panels/**`。这一只因此只做两件事——把外壳递来的
// 三包状态摊给展示组件，把事件接回来。props 一次摊开而不是 v-bind 一整包：这二十来样东
// 西就是这一格的接口，谁传谁看得见；将来哪一样不传了，typecheck 也会点名。
import type { DocPeopleBundle } from '../../composables/useDocPeople'
import type { DocThreadsBundle } from '../../composables/useDocThreads'
import type { PanelDocBundle, PanelDocument } from '../../composables/usePanelDoc'
import type { Topic } from '../../cx_types'
import type { DocReviewRequest } from '../../lib/docReview'

import { ref, watch } from 'vue'

import PanelDocView from './PanelDocView.vue'

import { t } from '@/i18n'

const props = withDefaults(
  defineProps<{
    topic: Topic | null
    /** 打开的是这个房间里某个任务的实况文档。 */
    taskId?: string | null
    /** 项目资料库里的一份文档：直接打开它，标题在页上就能改。 */
    document?: PanelDocument | null
    // Bumped by the parent on AI activity (turn-done / update_doc tool) so the
    // panel reloads the doc 芝士 just wrote. See TopicView activityTick.
    activityTick: number
    // Project topics: the titles `<#id>` chips in the document show.
    topicList?: Topic[]
    /** 项目 AI 队友的名字：文档被它改过时，提示里说的是它，不写死「芝士」。 */
    agentName?: string
    /** 项目 AI 队友的 handle：评论里 @ 它要写成它的点名，它才收得到。 */
    agentHandle?: string | null
    /** 画在一整页里，见 PanelDocView。 */
    bare?: boolean
    /** 顶栏画到页面上的哪个位置，见 PanelDocView。 */
    barTo?: string
    /** 这一格的取数（`composables/usePanelDoc.ts` 那一包）。 */
    docPanel: PanelDocBundle
    /** 评论串那一包（`composables/useDocThreads.ts`）。 */
    docThreads: DocThreadsBundle
    /** 名册读成名字那一包（`composables/useDocPeople.ts`）。 */
    docPeople: DocPeopleBundle
  }>(),
  {
    taskId: null,
    document: null,
    topicList: () => [],
    agentName: () => t('work.room.defaultAgentName'),
    agentHandle: null,
    bare: false,
    barTo: undefined,
  }
)

// open-topic: a `<#id>` chip was clicked — the parent navigates to that topic.
// open-file: a <&path> chip was clicked — WorkPanel switches to the
// 改动 tab and opens it there (the ONE cross-tab wire, and the only one).
const emit = defineEmits<{
  (e: 'open-topic', topicId: string): void
  (e: 'mention-click', handle: string): void
  (e: 'open-file', path: string): void
  /** 资料库文档的标题变了（自己改的，或者别人改的）。 */
  (e: 'titled', title: string): void
  /** 「⋯」里点了删除：问不问、怎么删、删完去哪，由这一页定。 */
  (e: 'delete'): void
}>()

const viewRef = ref<InstanceType<typeof PanelDocView> | null>(null)

const doc = props.docPanel
const docThreads = props.docThreads
const people = props.docPeople

// 回复里 @ 的 AI 队友，和发评论一样写成点名。
const threads = {
  state: docThreads.state,
  actions: {
    ...docThreads.actions,
    reply: (id: string, content: string) => docThreads.actions.reply(id, doc.withMentions(content)),
  },
}

// Dev-only probe hook: lets Playwright inspect the live document without
// guessing at DOM classes (observability rule).
if (import.meta.env.DEV) {
  ;(window as unknown as Record<string, unknown>).__docPanel = {
    getMarkdown: () => viewRef.value?.serializeVisual() ?? null,
    connection: () => doc.connection.value,
  }
}

function pulse() {
  void viewRef.value?.pulse()
}
function highlightTurn(turnId: string) {
  viewRef.value?.highlightTurn(turnId)
}
function reviewEdits(request: DocReviewRequest) {
  viewRef.value?.reviewEdits(request)
}

// ---- 资料库文档的标题 ----
const title = ref(props.document?.title ?? '')
watch(
  () => props.document?.title,
  (next) => {
    if (next !== undefined) title.value = next
  }
)
async function rename(next: string) {
  if (!props.document || next === title.value) return
  const before = title.value
  title.value = next
  try {
    emit('titled', await doc.rename(next))
  } catch (cause) {
    title.value = before
    doc.setError(cause instanceof Error ? cause.message : String(cause))
  }
}
// 别人改了名，或者只拿到了编号（地址上点名的那一格）：读一次它现在叫什么。
async function readTitle() {
  const asked = props.document?.id
  if (!asked) return
  const current = await doc.currentTitle().catch(() => null)
  if (current !== null && props.document?.id === asked) {
    title.value = current
    emit('titled', current)
  }
}
watch(doc.renames, readTitle)
watch(
  () => props.document?.id,
  (id) => {
    if (id && !props.document?.title) void readTitle()
  },
  { immediate: true }
)

defineExpose({ pulse, highlightTurn, reviewEdits })
</script>

<template>
  <PanelDocView
    ref="viewRef"
    :topic="props.topic"
    :document="props.document ? { ...props.document, title } : null"
    :deleted="doc.deleted.value"
    :activity-tick="props.activityTick"
    :topic-list="props.topicList"
    :agent-name="props.agentName"
    :agent-handle="props.agentHandle"
    :mention-names="people.names.value"
    :mention-people="people.people.value"
    :bare="props.bare"
    :untitled="!!props.taskId"
    :bar-to="props.barTo"
    :session="doc.session.value"
    :editable="doc.editable.value"
    :read-only="doc.readOnly.value"
    :loading="doc.loading.value"
    :connection="doc.connection.value"
    :outdated="doc.outdated.value"
    :peers="doc.peers.value"
    :error-msg="doc.errorMsg.value"
    :comment-author="doc.commentAuthor"
    :send-comment="doc.sendComment"
    :refresh-threads="docThreads.refresh"
    :thread-state="threads.state"
    :thread-actions="threads.actions"
    :suggestion-reasons="doc.suggestionReasons.value"
    :fetch-suggestion-reasons="doc.fetchSuggestionReasons"
    :overview-blocks="doc.overviewBlocks.value"
    :overview-failed="doc.overviewFailed.value"
    :reload-overview="doc.loadOverview"
    :fetch-doc-nodes="doc.fetchDocNodes"
    :image-src="doc.imageSrc"
    :toggle-editable="doc.toggleEditable"
    :set-error="doc.setError"
    :ask-agent="doc.askAgent"
    :stop-agent="doc.stopAgent"
    :answer-to-comment="doc.answerToComment"
    :apply-doc-edits="doc.applyEdits"
    :last-edit="doc.lastEdit.value"
    :name-of="doc.nameOf"
    :load-versions="doc.loadVersions"
    :restore-version="doc.restoreVersion"
    @open-topic="emit('open-topic', $event)"
    @mention-click="emit('mention-click', $event)"
    @open-file="emit('open-file', $event)"
    @rename="rename"
    @delete="emit('delete')"
  />
</template>
