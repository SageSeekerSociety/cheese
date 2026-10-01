<script setup lang="ts">
// 话题页右侧「文档」这一格 —— 一只薄容器。
//
// 它以前是一个 2439 行的组件：自己 import 五个接口函数、自己每 2.5 秒把正文写回去、自己
// 管着服务端版本号与冲突，然后又自己把这一切画出来。于是「文档」这个界面在测试和 /demo
// 里都得先立一个假后端，而任何一行样式调整都要在一个两千行的文件里找。
//
// 现在两边分家，和 #2130 的「改动」、#2158 的「预览」是同一个形状：
//   - 取数（正文的读写、评论与节点、自动保存的那只时钟、冲突与草稿的状态机）
//     → `composables/usePanelDoc.ts`
//   - 画（横条上写哪句话、源码模式、一栏正文、评论区、两个对话框）
//     → `components/panels/PanelDocView.vue`，只凭 props 渲染
//   - 编辑器本身（tiptap 实例、三种装饰、段落闪一下）
//     → `components/panels/doc/DocSurface.vue`，画不动的一层放在那儿
// 这一只只负责把两边接起来：状态递下去、事件接回来。加取数动作在组合式函数里加，加画法
// 在展示组件里加，这一只基本不再长。props 一次摊开而不是 v-bind 一整包：这三十来样东西
// 就是这一格的接口，谁传谁看得见；将来哪一样不传了，typecheck 也会点名。
import type { Topic } from '../../cx_types'

import { ref } from 'vue'

import { usePanelDoc } from '../../composables/usePanelDoc'

import PanelDocView from './PanelDocView.vue'

import { t } from '@/i18n'

const props = withDefaults(
  defineProps<{
    topic: Topic | null
    // Bumped by the parent on AI activity (turn-done / update_doc tool) so the
    // panel reloads the doc 芝士 just wrote. See TopicView activityTick.
    activityTick: number
    // Project topics (A2): resolve a doc node's upgraded_to_topic_id to the
    // subtopic's title + live status for the in-place live-ref badge.
    topicList?: Topic[]
    /** 项目 AI 队友的名字：文档被它改过时，提示里说的是它，不写死「芝士」。 */
    agentName?: string
  }>(),
  { topicList: () => [], agentName: () => t('work.room.defaultAgentName') }
)

// open-topic (A2): a doc live-ref chip was clicked — the parent navigates to the
// subtopic. open-file: a <&path> chip was clicked — WorkPanel switches to the
// 改动 tab and opens it there (the ONE cross-tab wire, and the only one).
const emit = defineEmits<{
  (e: 'open-topic', topicId: string): void
  (e: 'mention-click', handle: string): void
  (e: 'open-file', path: string): void
  // 总览自动区里的一条决策 / 里程碑：去向不在话题里，交给拿着路由的那一层。
  (e: 'open-resource', resource: 'milestone'): void
}>()

// 展示组件也是组合式函数要的那两个口子：读编辑器里现在这一版、把服务端那一版装进去。
// 中间隔着两层（这一层 → 展示组件 → 编辑器），所以两个口子都在这儿现接 —— 组合式函数
// 在 setup 里就要拿到它们，那时 ref 还是空的，所以只能给箭头函数（调用发生在挂载之后）。
const viewRef = ref<InstanceType<typeof PanelDocView> | null>(null)

const {
  editable,
  editingBlocked,
  mdAndUp,
  loading,
  saveStatus,
  paused,
  pausedHint,
  errorMsg,
  lossy,
  lossyConfirmOpen,
  sourceMode,
  sourceDraft,
  pendingEdits,
  hasPendingEdits,
  externalDoc,
  comments,
  anchorNodes,
  liveRefIndex,
  commentMarkIndex,
  refreshComments,
  commentAuthor,
  sendComment,
  save,
  confirmLossySave,
  onBlur: handleBlur,
  toggleEditable,
  toggleSourceMode,
  enterSourceMode,
  applyPendingEdits,
  discardPendingEdits,
  viewExternalDoc,
  overwriteWithMine,
  onSourceInput: handleSourceInput,
  onDocKeydown: handleDocKeydown,
  fetchDocNodes,
  imageSrc,
  dirty,
  markEdited,
  setError,
} = usePanelDoc(props, {
  serializeVisual: () => viewRef.value?.serializeVisual() ?? null,
  installMarkdown: (body) => viewRef.value?.installMarkdown(body),
})

// Dev-only probe hook: lets Playwright inspect serialization/dirty state
// without guessing at DOM classes (observability rule). The counters are read off
// this object by the surface's onUpdate, so it has to exist before the editor does.
if (import.meta.env.DEV) {
  ;(window as unknown as Record<string, unknown>).__docPanel = {
    getMarkdown: () => viewRef.value?.serializeVisual() ?? null,
    isDirty: () => dirty.value,
    // 军规 1 state: probes assert that nothing was dropped, not that a class
    // name happened to render.
    saveStatus: () => saveStatus.value,
    isAutosavePaused: () => paused.value,
    pendingEdits: () => [...pendingEdits.value],
    externalDoc: () => externalDoc.value,
    updates: 0,
  }
}

function pulse() {
  void viewRef.value?.pulse()
}
function highlightTurn(turnId: string) {
  viewRef.value?.highlightTurn(turnId)
}

defineExpose({ pulse, highlightTurn })
</script>

<template>
  <PanelDocView
    ref="viewRef"
    :topic="props.topic"
    :activity-tick="props.activityTick"
    :topic-list="props.topicList"
    :agent-name="props.agentName"
    :md-and-up="mdAndUp"
    :editable="editable"
    :editing-blocked="editingBlocked"
    :loading="loading"
    :save-status="saveStatus"
    :paused="paused"
    :paused-hint="pausedHint"
    :error-msg="errorMsg"
    :lossy="lossy"
    :lossy-confirm-open="lossyConfirmOpen"
    :source-mode="sourceMode"
    :source-draft="sourceDraft"
    :pending-edits="pendingEdits"
    :has-pending-edits="hasPendingEdits"
    :external-doc="externalDoc"
    :comments="comments"
    :comment-author="commentAuthor"
    :send-comment="sendComment"
    :anchor-nodes="anchorNodes"
    :live-ref-index="liveRefIndex"
    :comment-mark-index="commentMarkIndex"
    :fetch-doc-nodes="fetchDocNodes"
    :image-src="imageSrc"
    :save="save"
    :handle-blur="handleBlur"
    :handle-doc-keydown="handleDocKeydown"
    :handle-source-input="handleSourceInput"
    :confirm-lossy-save="confirmLossySave"
    :refresh-comments="refreshComments"
    :toggle-editable="toggleEditable"
    :toggle-source-mode="toggleSourceMode"
    :enter-source-mode="enterSourceMode"
    :apply-pending-edits="applyPendingEdits"
    :discard-pending-edits="discardPendingEdits"
    :view-external-doc="viewExternalDoc"
    :overwrite-with-mine="overwriteWithMine"
    :set-error="setError"
    @open-topic="emit('open-topic', $event)"
    @mention-click="emit('mention-click', $event)"
    @open-file="emit('open-file', $event)"
    @open-resource="emit('open-resource', $event)"
    @edited="markEdited"
    @close-lossy-confirm="lossyConfirmOpen = false"
  />
</template>
