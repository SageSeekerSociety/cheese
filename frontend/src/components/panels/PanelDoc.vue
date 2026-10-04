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
// 这一只只负责把两边接起来：状态递下去、事件接回来。加取数动作在组合式函数里加，加画法
// 在展示组件里加，这一只基本不再长。props 一次摊开而不是 v-bind 一整包：这二十来样东西
// 就是这一格的接口，谁传谁看得见；将来哪一样不传了，typecheck 也会点名。
import type { ProjectMemberRow, Topic } from '../../cx_types'
import type { DocReviewRequest } from '../../lib/docReview'

import { ref } from 'vue'

import { useDocPeople } from '../../composables/useDocPeople'
import { useDocThreads } from '../../composables/useDocThreads'
import { usePanelDoc } from '../../composables/usePanelDoc'

import PanelDocView from './PanelDocView.vue'

import { t } from '@/i18n'

const props = withDefaults(
  defineProps<{
    topic: Topic | null
    /** 打开的是这个房间里某个任务的实况文档。 */
    taskId?: string | null
    // Bumped by the parent on AI activity (turn-done / update_doc tool) so the
    // panel reloads the doc 芝士 just wrote. See TopicView activityTick.
    activityTick: number
    // Project topics: the titles `<#id>` chips in the document show.
    topicList?: Topic[]
    /** 项目 AI 队友的名字：文档被它改过时，提示里说的是它，不写死「芝士」。 */
    agentName?: string
    /** 项目 AI 队友的 handle：评论里 @ 它要写成它的点名，它才收得到。 */
    agentHandle?: string | null
    /** 项目名册：正文里 @ 得到的人，`<@账号>` 标签上的名字。 */
    members?: ProjectMemberRow[]
    /** 画在一整页里，见 PanelDocView。 */
    bare?: boolean
    /** 顶栏画到页面上的哪个位置，见 PanelDocView。 */
    barTo?: string
  }>(),
  {
    taskId: null,
    topicList: () => [],
    agentName: () => t('work.room.defaultAgentName'),
    agentHandle: null,
    members: () => [],
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
}>()

const viewRef = ref<InstanceType<typeof PanelDocView> | null>(null)

const doc = usePanelDoc(props)

const docThreads = useDocThreads(() => doc.documentId.value)
const people = useDocPeople({
  members: () => props.members,
  agentHandle: () => props.agentHandle,
  agentName: () => props.agentName,
})
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

defineExpose({ pulse, highlightTurn, reviewEdits })
</script>

<template>
  <PanelDocView
    ref="viewRef"
    :topic="props.topic"
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
  />
</template>
