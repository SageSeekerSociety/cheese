<script setup lang="ts">
// 文档那一格的**画**：顶栏、正文、共用的工具侧栏。
//
// 它只凭 props 渲染，不认识接口也不认识路由 —— 打开协同文档、评论、节点都在
// composables/usePanelDoc.ts 里（#2143）。正文编辑器本身在 doc/DocSurface.vue：这里画出
// 它的位置，把取来的东西递下去，把底下发上来的动作接住。正文不经过这里：编辑器直接绑
// 在协同文档（`session`）上，没有一个「保存」要这一层去管。
import type { DocConnection, DocPeer, DocSession } from '../../composables/useDocCollab'
import type { SendDocComment } from '../../composables/useDocCommentDraft'
import type { PanelDocument } from '../../composables/usePanelDoc'
import type { MentionPoolEntry } from '../../composables/useRoomMentionPicker'
import type { Block, Topic } from '../../cx_types'
import type { DocAgentListener, DocAgentRequest } from '../../lib/docAgent'
import type { CommentSpot } from '../../lib/docCommentSpots'
import type { DocEdit } from '../../lib/docEdits'
import type { DocVersionPage } from '../../lib/docHistory'
import type { DocReviewRequest } from '../../lib/docReview'
import type { DocThreadActions, DocThreadState, ThreadPlace } from '../../lib/docThreadTypes'

import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

import { scrollBehavior } from '@/utils/motion'

import { useDocAgent } from '../../composables/useDocAgent'
import { useDocFind } from '../../composables/useDocFind'
import { useDocOutline } from '../../composables/useDocOutline'
import { useDocReview } from '../../composables/useDocReview'
import { useDocSuggestions } from '../../composables/useDocSuggestions'
import { anchorComment } from '../../lib/docCommentSpots'
import { exportMarkdown } from '../../lib/docSchema'
import { topicTitle } from '../../lib/topicState'

import DocCommentPanel from './doc/DocCommentPanel.vue'
import DocEditLayer from './doc/DocEditLayer.vue'
import DocFindBar from './doc/DocFindBar.vue'
import DocHistory from './doc/DocHistory.vue'
import DocReviewStrip from './doc/DocReviewStrip.vue'
import DocSuggestionStrip from './doc/DocSuggestionStrip.vue'
import DocSurface from './doc/DocSurface.vue'
import DocTopBar from './doc/DocTopBar.vue'
import OverviewAuto from './doc/OverviewAuto.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

const props = withDefaults(
  defineProps<{
    topic: Topic | null
    /** 项目资料库里的一份文档：大标题是它自己的，在页上就能改。 */
    document?: PanelDocument | null
    /** 打开着的时候被人删了。 */
    deleted?: boolean
    /** 父层在 AI 动过之后加一：总览那一块据此重读。 */
    activityTick: number
    /** 项目 AI 队友的名字。 */
    agentName?: string
    /** 项目 AI 队友的 handle：评论里点它的名写成它的名字。 */
    agentHandle?: string | null
    /** 账号 → 名字：正文和评论里的 `<@账号>` 标签上写的字。 */
    mentionNames?: Record<string, string>
    /** 正文里打 @ 时列出来的人。 */
    mentionPeople?: MentionPoolEntry[]
    /** 项目话题表：正文里的 `<#id>` chip 靠它认名字。 */
    topicList?: Topic[]
    /** 画在一整页里（项目文档的章程）：页头已经说了这是什么，不再画大标题和总览自动区。 */
    bare?: boolean
    /** 顶栏画到页面上的这个位置（CSS 选择器），和页面自己的那一行并成一行。 */
    barTo?: string
    // ---- 这一篇现在是什么状态 ----
    /** 这一篇的协同文档；还没打开时是 null。 */
    session: DocSession | null
    /** 能不能改：没有编辑权限，或者自己切到了只读。 */
    editable: boolean
    /** 没有编辑权限（凭证说的），切不回编辑。 */
    readOnly: boolean
    /** 正文还在路上。 */
    loading: boolean
    connection: DocConnection
    /** 协同服务已换了新版的文档格式，这一页得刷新才能再打开文档。 */
    outdated?: boolean
    /** 同一篇文档打开着的其他人。 */
    peers: DocPeer[]
    errorMsg: string | null
    // ---- 评论区 ----
    commentAuthor?: string
    threadState: DocThreadState
    threadActions: DocThreadActions
    /** 发一条评论（评的是 `quote` 那几个字）；回执里有它的 id，标记由这一层放到字上。 */
    sendComment?: (content: string, quote: string) => Promise<{ id: string }>
    /** 重读评论串。 */
    refreshThreads?: () => Promise<void>
    // ---- 装饰的原料（原样递给正文那一半） ----
    /** 修改建议的理由（建议 id → 理由），卡上写出来。 */
    suggestionReasons?: Record<string, string>
    /** 文档里有了新的修改建议时调一下：读它们的理由。 */
    fetchSuggestionReasons?: () => void
    /** 取一份最新的节点树（闪某一段要它）。 */
    fetchDocNodes: () => Promise<Block[]>
    /** 图片 src 的显示期解析。 */
    imageSrc: (src: string) => string
    // ---- 动作 ----
    toggleEditable: () => void
    setError: (message: string | null) => void
    /** 在文档里找 AI 队友：问一次，答的过程交给 `listener`；没有时不给这个入口。 */
    askAgent?: (request: DocAgentRequest, listener: DocAgentListener) => Promise<void>
    /** 停下这一问。 */
    stopAgent?: (conversation: string) => Promise<unknown>
    /** 把这一问的回答放进一条新评论；回执是那条评论的 id。 */
    answerToComment?: (conversation: string, quote: string, question: string) => Promise<string>
    /** 以自己的名义替换正文里的字（撤销、还原 AI 队友的修改）。 */
    applyDocEdits?: (edits: DocEdit[]) => Promise<unknown>
    // ---- 修改记录 ----
    /** 最近一次编辑：谁（读成名字）、什么时候。 */
    lastEdit?: { name: string; at: string } | null
    /** handle 读成名字。 */
    nameOf?: (handle: string) => string
    loadVersions?: (before?: number) => Promise<DocVersionPage>
    restoreVersion?: (version: number, expected: number) => Promise<unknown>
  }>(),
  {
    document: null,
    deleted: false,
    agentName: () => t('work.room.defaultAgentName'),
    agentHandle: null,
    mentionNames: undefined,
    mentionPeople: () => [],
    topicList: () => [],
    bare: false,
    barTo: undefined,
    outdated: false,
    commentAuthor: '',
    sendComment: undefined,
    refreshThreads: undefined,
    askAgent: undefined,
    stopAgent: undefined,
    answerToComment: undefined,
    suggestionReasons: () => ({}),
    fetchSuggestionReasons: undefined,
    applyDocEdits: undefined,
    lastEdit: null,
    nameOf: (handle: string) => handle,
    loadVersions: undefined,
    restoreVersion: undefined,
  }
)

const emit = defineEmits<{
  (e: 'open-topic', topicId: string): void
  (e: 'mention-click', handle: string): void
  (e: 'open-file', path: string): void
  /** 资料库文档的标题改成了这样。 */
  (e: 'rename', title: string): void
  (e: 'delete'): void
}>()

/** 大标题：对话的名字，或者资料库文档自己的名字（没起名时是「未命名文档」）。 */
const docTitle = computed(() =>
  props.document ? props.document.title || t('work.room.doc.untitled') : props.topic ? topicTitle(props.topic) : ''
)

// 评论区自己是一个组件：列表、折叠、写评论的输入框都在里面。这一层只负责把它开出来 ——
// 抛上去的那两件事（锚点 + 引文）它自己接，因为 ref 就在这一层。
const commentsRef = ref<InstanceType<typeof DocCommentPanel> | null>(null)
const openId = ref<string | null>(null)
const bodyRef = ref<HTMLElement | null>(null)
let pulseTimer: ReturnType<typeof setTimeout> | undefined

// B1 Phase 2 (cross-view link, panel-level): when a chat action that changed the
// doc is clicked, flash the document + scroll it into view — connecting the
// process (conversation) to the state (doc). The paragraph-level flash itself
// belongs to the surface (it is the one holding the editor); this is the
// whole-page fallback it calls when it cannot locate the paragraphs.
const pulsing = ref(false)

async function pulse() {
  bodyRef.value?.scrollTo({ top: 0, behavior: scrollBehavior() })
  if (pulseTimer) clearTimeout(pulseTimer)
  pulsing.value = false
  await nextTick()
  pulsing.value = true
  pulseTimer = setTimeout(() => {
    pulsing.value = false
  }, 1200)
}
onBeforeUnmount(() => {
  if (pulseTimer) clearTimeout(pulseTimer)
})

// 正文区的滚动：代码块工具条贴在 <pre> 上，正文一滚它就指错地方了。往上发一次
// 「滚了」，由拿着编辑器的正文那一半收起来。
const scrollTick = ref(0)
function reload() {
  window.location.reload()
}

function onBodyScroll() {
  scrollTick.value++
}

const surfaceRef = ref<InstanceType<typeof DocSurface> | null>(null)
function highlightTurn(turnId: string) {
  void surfaceRef.value?.highlightTurn(turnId)
}
function openComment(spot: CommentSpot) {
  commentsRef.value?.open(spot)
}
/** 发出一条评论，再把它的标记放到评的那几个字上。 */
const postComment: SendDocComment = async (_topicId, content, spot) => {
  if (!props.sendComment) throw new Error(t('work.room.comments.unavailable'))
  const posted = await props.sendComment(content, spot.quote)
  const ed = surfaceRef.value?.editor
  if (ed && spot.quote) anchorComment(ed, spot, posted.id)
  openId.value = posted.id
  await props.refreshThreads?.()
}
// 评论：没解决的那些在正文里标出来，正在看的那一串标得重些。
const openThreads = computed(
  () =>
    new Set(props.threadState.threads.filter((thread) => thread.state === 'open').map((thread) => thread.comment.id))
)
function placeOfThread(id: string): ThreadPlace {
  return surfaceRef.value?.threadPlace(id) ?? null
}
function revealThread(id: string) {
  surfaceRef.value?.revealThread(id)
}

// 大纲与文档内查找：都只读正文那一半的编辑器（正文编辑器住在 DocSurface 里），这一
// 层拿着它算出标题、匹配，并把点击 / 上下跳落成滚动。逻辑本身在各自的 composable 与
// lib 里，这里只有接线。
const editorOf = () => surfaceRef.value?.editor ?? null
const { headings: outlineHeadings, go: goHeading } = useDocOutline(editorOf)
const {
  query: findQuery,
  open: findOpen,
  total: findTotal,
  current: findCurrent,
  setQuery: setFindQuery,
  step: stepFind,
  setOpen: setFindOpen,
  toggle: toggleFind,
} = useDocFind(editorOf)

// 点名（`<@handle>`）读成名字：外面给了名册就用名册，没给至少认得 AI 队友。
const mentionNames = computed<Record<string, string>>(
  () => props.mentionNames ?? (props.agentHandle ? { [props.agentHandle]: props.agentName } : {})
)

const agentOptions = {
  editor: () => surfaceRef.value?.editor ?? null,
  ask: () => props.askAgent,
  stop: () => props.stopAgent,
  editable: () => props.editable,
  applyEdits: () => props.applyDocEdits,
  toComment: () => props.answerToComment,
  agentName: () => props.agentName,
  onError: (message: string) => props.setError(message),
}
// 选中一段找 AI 队友（浮条上那一个），和对整篇找它（顶栏上那一个）。
const rewrite = useDocAgent({ ...agentOptions, scope: 'selection' })
const docAgent = useDocAgent({ ...agentOptions, scope: 'document' })
const canAskDocument = computed(() => !!props.agentHandle && !!props.askAgent)
const suggestions = useDocSuggestions(() => surfaceRef.value?.editor ?? null)
// 建议那一条：点顶栏上的「N 处建议」打开，从第一处看起。
const suggestionsOpen = ref(false)
function toggleSuggestions() {
  suggestionsOpen.value = !suggestionsOpen.value
  if (suggestionsOpen.value) suggestions.step(1)
}
function dismissSuggestions() {
  suggestions.dismissDone()
  suggestionsOpen.value = false
}
const historyOpen = ref(false)
/** 导出 Markdown：文档现在的样子，待处理的建议不算在内。 */
function exportDoc() {
  if (!props.session) return
  const blob = new Blob([exportMarkdown(props.session.doc)], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `${docTitle.value || 'document'}.md`
  link.click()
  URL.revokeObjectURL(url)
}
watch(
  () => suggestions.list.value.map((s) => s.id).join(' '),
  (ids, before) => {
    if (ids && ids.split(' ').some((id) => !before?.split(' ').includes(id))) props.fetchSuggestionReasons?.()
  }
)
const review = useDocReview({
  editor: () => surfaceRef.value?.editor ?? null,
  applyEdits: () => props.applyDocEdits,
  onError: (message) => props.setError(message),
})
/** 「查看改动」：在正文里一处处标出某个人让 AI 队友改的那几处。 */
function reviewEdits(request: DocReviewRequest) {
  rewrite.close()
  review.open(request)
}
function locateComment(commentId: string) {
  commentsRef.value?.locate(commentId)
}
watch([() => props.topic?.id, () => props.document?.id, () => props.commentAuthor], () => {
  openId.value = null
  rewrite.close()
  review.close()
  docAgent.close()
  suggestionsOpen.value = false
  historyOpen.value = false
  setFindOpen(false)
})

// 编辑器里现在这一版正文（开发时的探针读它），只有这里知道编辑器在哪。
defineExpose({
  pulse,
  highlightTurn,
  reviewEdits,
  serializeVisual: () => surfaceRef.value?.serializeVisual() ?? null,
})
</script>

<template>
  <!-- 根元素上不要放 Vuetify 的 display 工具类：WorkPanel 用 v-show 切 tab，而
       `.d-flex` 是 display: flex !important，会盖掉 v-show 写进去的 inline
       display: none（见 src/vShowDisplayUtilities.spec.ts）。 -->
  <div class="doc">
    <div v-if="!topic && !document" class="flex-grow-1 d-flex align-center justify-center text-medium-emphasis">
      <div class="text-center">
        <v-icon size="48" class="mb-2 text-disabled">mdi-file-document-outline</v-icon>
        <div>{{ t('work.room.doc.pickTopic') }}</div>
      </div>
    </div>

    <div v-else-if="deleted" class="flex-grow-1 d-flex align-center justify-center text-medium-emphasis">
      <div class="t-body">{{ t('work.room.doc.deleted') }}</div>
    </div>

    <div v-else-if="outdated" class="flex-grow-1 d-flex align-center justify-center">
      <div class="text-center">
        <div class="t-body mb-3">{{ t('work.room.doc.outdated') }}</div>
        <BaseButton kind="secondary" size="sm" @click="reload">{{ t('work.room.doc.reload') }}</BaseButton>
      </div>
    </div>

    <template v-else>
      <!-- Stage: the editor + (optionally) a docked tool panel beside it. -->
      <div class="doc-stage flex-grow-1">
        <Teleport :to="barTo || 'body'" defer :disabled="!barTo">
          <DocTopBar
            class="doc-top"
            :class="{ 'doc-top--inline': !!barTo }"
            :loading="loading"
            :connection="connection"
            :peers="peers"
            :editable="editable"
            :read-only="readOnly"
            :last-edit="lastEdit"
            :suggestion-count="suggestions.list.value.length"
            :suggestions-open="suggestionsOpen"
            :comment-count="openThreads.size"
            :comments-open="commentsRef?.opened ?? false"
            :agent-name="agentName"
            :agent-handle="agentHandle"
            :agent="canAskDocument ? docAgent : undefined"
            :mention-names="mentionNames"
            :headings="outlineHeadings"
            :find-open="findOpen"
            :deletable="!!document && !readOnly"
            @delete="emit('delete')"
            @toggle-suggestions="toggleSuggestions"
            @toggle-comments="commentsRef?.toggle()"
            @toggle-editable="toggleEditable"
            @history="historyOpen = true"
            @export="exportDoc"
            @open-thread="locateComment"
            @toggle-find="toggleFind"
            @outline-select="goHeading"
          />
        </Teleport>
        <DocFindBar
          :open="findOpen"
          :query="findQuery"
          :total="findTotal"
          :current="findCurrent"
          @update:query="setFindQuery"
          @next="stepFind(1)"
          @prev="stepFind(-1)"
          @close="setFindOpen(false)"
        />
        <DocReviewStrip
          v-if="review.request.value"
          :agent-name="agentName"
          :requester="review.request.value.requester"
          :count="review.live.value.length"
          @step="review.step"
          @close="review.close"
        />
        <DocSuggestionStrip
          v-if="suggestionsOpen"
          :agent-name="agentName"
          :count="suggestions.list.value.length"
          :decided="suggestions.decided.value"
          :editable="editable"
          @step="suggestions.step"
          @accept-all="suggestions.decideAll(true)"
          @reject-all="suggestions.decideAll(false)"
          @dismiss="dismissSuggestions"
        />
        <!-- Editor surface — a Feishu Docs page: white, padded, centered column. -->
        <DocCommentPanel
          ref="commentsRef"
          v-model:open-id="openId"
          :topic-id="topic?.id ?? null"
          :author="commentAuthor"
          :send-comment="postComment"
          :thread-state="threadState"
          :thread-actions="threadActions"
          :place-of="placeOfThread"
          :agent-name="agentName"
          :mention-names="mentionNames"
          :name-of="nameOf"
          :writable="!readOnly"
          @locate="revealThread"
        >
          <div
            ref="bodyRef"
            class="doc-body overflow-y-auto"
            :class="{ readonly: !editable }"
            @scroll.passive="onBodyScroll"
          >
            <div class="doc-page" :class="{ 'doc-pulse': pulsing }">
              <!-- Large document title (Feishu Docs): the topic's, or the library document's own. -->
              <input
                v-if="document && !bare"
                :value="document.title"
                class="doc-page__title doc-page__title--input"
                autocomplete="off"
                :placeholder="t('work.room.doc.titlePlaceholder')"
                :aria-label="t('work.room.doc.titleLabel')"
                :readonly="!editable"
                maxlength="200"
                @keydown.enter.prevent="($event.target as HTMLInputElement).blur()"
                @blur="emit('rename', ($event.target as HTMLInputElement).value.trim())"
              />
              <h1 v-else-if="!bare" class="doc-page__title">{{ docTitle }}</h1>
              <!-- 正文本身。 -->
              <DocSurface
                ref="surfaceRef"
                :editable="editable"
                :loading="loading"
                :session="session"
                :title="docTitle"
                :placeholder="document ? t('work.room.doc.emptyPlaceholderLibrary') : ''"
                :topic-id="topic?.id ?? null"
                :topic-list="topicList"
                :mention-names="mentionNames"
                :mention-people="mentionPeople"
                :can-comment="!readOnly"
                :open-threads="openThreads"
                :active-thread="openId"
                :fetch-doc-nodes="fetchDocNodes"
                :image-src="imageSrc"
                :pulse="pulse"
                :scroll-tick="scrollTick"
                :agent-name="agentName"
                :agent-handle="agentHandle"
                @open-topic="emit('open-topic', $event)"
                @mention-click="emit('mention-click', $event)"
                @open-file="emit('open-file', $event)"
                @open-comment="openComment"
                @agent="rewrite.open($event)"
                @locate-comment="locateComment"
                @error="setError"
              />

              <DocEditLayer
                :editor="surfaceRef?.editor ?? null"
                :agent-name="agentName"
                :editable="editable"
                :rewrite="rewrite"
                :review="review"
                :suggestions="suggestions"
                :suggestion-reasons="suggestionReasons"
                :mention-names="mentionNames"
                @open-thread="locateComment"
              />

              <!-- 总览房间的其余两块（#1889 ②③）紧跟正文。评论在独立侧栏。只有根话题
                 有——别的房间的文档就是它自己那一份，没有人从那里看项目全局。 -->
              <OverviewAuto
                v-if="topic?.kind === 'root' && !bare"
                :topic="topic"
                :activity-tick="activityTick"
                @open-topic="emit('open-topic', $event)"
              />
            </div>
          </div>
        </DocCommentPanel>
      </div>
      <!-- /.doc-stage -->

      <DocHistory
        v-if="loadVersions"
        v-model:open="historyOpen"
        :load="loadVersions"
        :restore="restoreVersion"
        :editable="editable"
        :name-of="nameOf"
        :mention-names="mentionNames"
      />

      <!-- Floating, never clipped: the old flow-layout alert sat below the
           scroll stage and rendered half-hidden at the panel edge. -->
      <v-alert
        v-if="errorMsg"
        type="error"
        density="compact"
        class="doc-error-toast"
        closable
        @click:close="setError(null)"
      >
        {{ errorMsg }}
      </v-alert>
    </template>
  </div>
</template>

<style scoped>
.doc-top {
  flex: 0 0 auto;
  border-bottom: 0.5px solid var(--line);
  background: var(--surface);
}
/* 并进页面那一行时，下边线和底色是那一行的，整条靠右。 */
.doc-top--inline {
  flex: 0 1 auto;
  min-width: 0;
  padding: 0;
  border-bottom: none;
  background: transparent;
}
.doc-top--inline :deep(.doc-top-bar__state) {
  flex: 0 1 auto;
}
.doc {
  /* In the split workspace the doc is a full white surface that fills the pane —
     not a floating card on a gray canvas (which left gray gutters around it). */
  background: var(--surface);
  container-type: inline-size;
  position: relative;
  display: flex;
  flex-direction: column;
  height: 100%;
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
}
.doc-body {
  padding: 8px 0 80px;
  position: relative;
  display: flex;
  justify-content: center;
}
/* The document column: white surface (inherits .doc), text capped for
   readability and centered. No card border/radius — it IS the surface. */
.doc-page {
  position: relative;
  width: 100%;
  max-width: 48rem;
  background: transparent;
  padding: 16px 24px 72px;
}
@container (min-width: 48rem) {
  .doc-page {
    padding: 24px 44px 72px;
  }
}
/* 能悬停的设备上，行首的手柄（DocOverlays .doc-handle，42px）待在左边距里，不被裁掉。 */
@media (hover: hover) {
  .doc-page {
    padding-left: 48px;
  }
}
/* The topic title, above the document. */
.doc-page__title {
  max-width: 720px;
  margin: 0 auto 24px;
  font-family: var(--font-display);
  font-size: 22px;
  font-weight: 600;
  line-height: 1.5;
  letter-spacing: -0.02em;
  color: var(--ink);
}
/* A library document's title is typed in place. */
.doc-page__title--input {
  display: block;
  width: 100%;
  padding: 0;
  border: 0;
  outline: none;
  background: transparent;
}
.doc-page__title--input::placeholder {
  color: var(--faint);
}

/* B1 Phase 2: a brief highlight when a chat action points at the doc. */
.doc-pulse {
  animation: docPulse 1.2s ease-out;
}
@keyframes docPulse {
  0% {
    box-shadow: 0 0 0 3px var(--accent);
    background: color-mix(in srgb, var(--accent) 8%, transparent);
  }
  100% {
    box-shadow: 0 0 0 0 transparent;
    background: transparent;
  }
}
/* Stage holds the editor and, when pinned, the docked tool panel beside it. */
.doc-stage {
  position: relative;
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}
.doc-body {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
}
.md-content :deep(p) {
  margin: 0 0 6px;
}
.md-content :deep(p:last-child) {
  margin-bottom: 0;
}
.md-content :deep(code) {
  font-family: var(--font-mono);
  background: var(--fill);
  padding: 0.5px 5px;
  border-radius: var(--radius-sm);
  font-size: 0.88em;
}
.md-content :deep(pre) {
  background: var(--fill);
  padding: 10px 12px;
  border-radius: 8px;
  overflow-x: auto;
}

.doc-error-toast {
  position: absolute;
  left: 50%;
  bottom: 18px;
  transform: translateX(-50%);
  z-index: var(--z-shell);
  max-width: min(560px, calc(100% - 32px));
  overflow-wrap: anywhere;
  box-shadow: var(--shadow-2);
}
</style>
