<script setup lang="ts">
// 话题页右侧「文档」这一格 —— 一只薄容器。
//
// 它以前是一个 2439 行的组件：自己 import 五个接口函数、自己每 2.5 秒把正文写回去、自己
// 管着服务端版本号与冲突，然后又自己把这一切画出来。于是「文档」这个界面在测试和 /demo
// 里都得先立一个假后端，而任何一行样式调整都要在一个两千行的文件里找。
//
// 现在两边分家，和 #2130 的「改动」、#2158 的「预览」是同一个形状：
//   - 取数（打开协同文档、已存的那一版、评论与节点）
//     → `composables/usePanelDoc.ts`
//   - 画（横条上写哪句话、一栏正文、评论区）
//     → `components/panels/PanelDocView.vue`，只凭 props 渲染
//   - 编辑器本身（tiptap 实例、几种装饰、别人的光标、段落闪一下）
//     → `components/panels/doc/DocSurface.vue`，画不动的一层放在那儿
// 这一只只负责把两边接起来：状态递下去、事件接回来。加取数动作在组合式函数里加，加画法
// 在展示组件里加，这一只基本不再长。props 一次摊开而不是 v-bind 一整包：这二十来样东西
// 就是这一格的接口，谁传谁看得见；将来哪一样不传了，typecheck 也会点名。
import type { Topic } from '../../cx_types'

import { ref } from 'vue'

import { useDocAi } from '../../composables/useDocAi'
import { useDocThreads } from '../../composables/useDocThreads'
import { usePanelDoc } from '../../composables/usePanelDoc'

import DocAiPanel from './doc/DocAiPanel.vue'
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
    /** 画在一整页里，见 PanelDocView。 */
    bare?: boolean
  }>(),
  { topicList: () => [], agentName: () => t('work.room.defaultAgentName'), bare: false }
)

// open-topic (A2): a doc live-ref chip was clicked — the parent navigates to the
// subtopic. open-file: a <&path> chip was clicked — WorkPanel switches to the
// 改动 tab and opens it there (the ONE cross-tab wire, and the only one).
const emit = defineEmits<{
  (e: 'open-topic', topicId: string): void
  (e: 'mention-click', handle: string): void
  (e: 'open-file', path: string): void
}>()

// 组合式函数要读编辑器里现在这一版（文档 AI 拿它和已存的那一版比）。中间隔着两层（这
// 一层 → 展示组件 → 编辑器），组合式函数在 setup 里就要拿到它，那时 ref 还是空的，所以只
// 能给箭头函数（调用发生在挂载之后）。
const viewRef = ref<InstanceType<typeof PanelDocView> | null>(null)

const doc = usePanelDoc(props, {
  serializeVisual: () => viewRef.value?.serializeVisual() ?? null,
})

const threads = useDocThreads(() => props.topic?.id ?? null)
// 文档 AI 的选区按已存原文定位：屏幕上这一份和已存的那一版不一致时（还有字没存回、
// 还没连上），它既不提问也不采纳，等存回之后再说。
const aiBlocked = () => doc.loading.value || !doc.matchesStored()
const ai = useDocAi({
  topic: () => props.topic?.id ?? null,
  raw: () => doc.rawDoc.value,
  prefix: () => '',
  version: () => doc.docVersion.value,
  blocked: aiBlocked,
  reload: doc.reloadStored,
})

// Dev-only probe hook: lets Playwright inspect the live document without
// guessing at DOM classes (observability rule).
if (import.meta.env.DEV) {
  ;(window as unknown as Record<string, unknown>).__docPanel = {
    getMarkdown: () => viewRef.value?.serializeVisual() ?? null,
    connection: () => doc.connection.value,
    matchesStored: () => doc.matchesStored(),
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
    :bare="props.bare"
    :session="doc.session.value"
    :editable="doc.editable.value"
    :read-only="doc.readOnly.value"
    :loading="doc.loading.value"
    :connection="doc.connection.value"
    :peers="doc.peers.value"
    :error-msg="doc.errorMsg.value"
    :ai-opened="ai.opened.value"
    :comments="doc.comments.value"
    :comment-author="doc.commentAuthor"
    :send-comment="doc.sendComment"
    :thread-state="threads.state"
    :thread-actions="threads.actions"
    :anchor-nodes="doc.anchorNodes.value"
    :live-ref-index="doc.liveRefIndex.value"
    :comment-mark-index="doc.commentMarkIndex.value"
    :fetch-doc-nodes="doc.fetchDocNodes"
    :image-src="doc.imageSrc"
    :refresh-comments="doc.refreshComments"
    :toggle-editable="doc.toggleEditable"
    :set-error="doc.setError"
    @open-topic="emit('open-topic', $event)"
    @mention-click="emit('mention-click', $event)"
    @open-file="emit('open-file', $event)"
    @open-ai="ai.prepare($event)"
    @close-ai="ai.opened.value = false"
  >
    <template v-if="!props.bare" #ai>
      <DocAiPanel
        docked
        :opened="ai.opened.value"
        :cards="ai.cards.value"
        :question="ai.question.value"
        :busy="ai.busy.value"
        :error="ai.error.value"
        :selection-status="ai.selectionStatus.value"
        :prepared-context="ai.preparedContext.value"
        :has-selection="ai.preparedContext.value.state === 'verified' && !!ai.selection.value"
        :blocked="aiBlocked()"
        :unknown="!!ai.unknown.value"
        :version="doc.docVersion.value"
        @update:question="ai.question.value = $event"
        @submit="ai.submit"
        @accept="ai.accept"
        @cancel="ai.cancel"
        @recover="ai.recover"
        @close="ai.opened.value = false"
      />
    </template>
  </PanelDocView>
</template>
