<script setup lang="ts">
// 文档那一格的**画**：横条、正文、共用的工具侧栏。
//
// 它只凭 props 渲染，不认识接口也不认识路由 —— 打开协同文档、评论、节点都在
// composables/usePanelDoc.ts 里（#2143）。正文编辑器本身在 doc/DocSurface.vue：这里画出
// 它的位置，把取来的东西递下去，把底下发上来的动作接住。正文不经过这里：编辑器直接绑
// 在协同文档（`session`）上，没有一个「保存」要这一层去管。
import type { DocConnection, DocPeer, DocSession } from '../../composables/useDocCollab'
import type { SendDocComment } from '../../composables/useDocCommentDraft'
import type { Block, Topic } from '../../cx_types'
import type { DocEdit, DocRewriteRequest, DocRewriteResult } from '../../lib/docEdits'
import type { DocThreadActions, DocThreadState } from '../../lib/docThreadTypes'

import { nextTick, onBeforeUnmount, ref, watch } from 'vue'

import { useDocRewrite } from '../../composables/useDocRewrite'
import { topicTitle } from '../../lib/topicState'

import DocCommentPanel from './doc/DocCommentPanel.vue'
import DocEditLayer from './doc/DocEditLayer.vue'
import DocFormatToolbar from './doc/DocFormatToolbar.vue'
import DocPresence from './doc/DocPresence.vue'
import DocSurface from './doc/DocSurface.vue'
import OverviewAuto from './doc/OverviewAuto.vue'

import { t } from '@/i18n'

const props = withDefaults(
  defineProps<{
    topic: Topic | null
    /** 父层在 AI 动过之后加一：总览那一块据此重读。 */
    activityTick: number
    /** 项目 AI 队友的名字。 */
    agentName?: string
    /** 项目话题表：正文里的支线徽章、`<#id>` chip 都靠它认名字与状态。 */
    topicList?: Topic[]
    /** 画在一整页里（项目文档的章程）：页头已经说了这是什么，不再画大标题和总览自动区。 */
    bare?: boolean
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
    /** 同一篇文档打开着的其他人。 */
    peers: DocPeer[]
    errorMsg: string | null
    // ---- 评论区 ----
    comments: Block[]
    commentAuthor?: string
    threadState?: DocThreadState
    threadActions?: DocThreadActions
    sendComment?: SendDocComment
    anchorNodes: Block[]
    // ---- 装饰的原料（原样递给正文那一半） ----
    liveRefIndex: Map<number, string>
    commentMarkIndex: Map<number, { id: string; quote: string }[]>
    /** 取一份最新的节点树（评论定锚点、闪某一段都要它）。 */
    fetchDocNodes: () => Promise<Block[]>
    /** 图片 src 的显示期解析。 */
    imageSrc: (src: string) => string
    // ---- 动作 ----
    refreshComments: () => Promise<void>
    toggleEditable: () => void
    setError: (message: string | null) => void
    /** 让 AI 队友改选中的字；没有时浮条上不给「让…改」。 */
    rewriteSelection?: (request: DocRewriteRequest) => Promise<DocRewriteResult>
    /** 以自己的名义替换正文里的字（撤销、还原 AI 队友的修改）。 */
    applyDocEdits?: (edits: DocEdit[]) => Promise<unknown>
  }>(),
  {
    agentName: () => t('work.room.defaultAgentName'),
    topicList: () => [],
    bare: false,
    commentAuthor: '',
    sendComment: undefined,
    rewriteSelection: undefined,
    applyDocEdits: undefined,
  }
)

const emit = defineEmits<{
  (e: 'open-topic', topicId: string): void
  (e: 'mention-click', handle: string): void
  (e: 'open-file', path: string): void
}>()

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
  bodyRef.value?.scrollTo({ top: 0, behavior: 'smooth' })
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
function onBodyScroll() {
  scrollTick.value++
}

const surfaceRef = ref<InstanceType<typeof DocSurface> | null>(null)
function highlightTurn(turnId: string) {
  void surfaceRef.value?.highlightTurn(turnId)
}
function highlightNode(nodeId: string) {
  void surfaceRef.value?.highlightNode(nodeId)
}
function openComment(payload: { anchorId: string | null; quote: string; ask: boolean }) {
  const { anchorId, quote } = payload
  commentsRef.value?.open({ anchorId, quote }, payload.ask ? `@${props.agentName} ` : undefined)
}

const rewrite = useDocRewrite({
  editor: () => surfaceRef.value?.editor ?? null,
  rewrite: () => props.rewriteSelection,
  applyEdits: () => props.applyDocEdits,
  agentName: () => props.agentName,
  onError: (message) => props.setError(message),
})
function locateComment(commentId: string) {
  commentsRef.value?.locate(commentId)
}
watch([() => props.topic?.id, () => props.commentAuthor], () => {
  openId.value = null
  rewrite.close()
})
function quoteState(id: string) {
  return surfaceRef.value?.commentQuoteState(id) ?? 'missing'
}

// 编辑器里现在这一版正文（开发时的探针读它），只有这里知道编辑器在哪。
defineExpose({
  pulse,
  highlightTurn,
  serializeVisual: () => surfaceRef.value?.serializeVisual() ?? null,
})
</script>

<template>
  <!-- 根元素上不要放 Vuetify 的 display 工具类：WorkPanel 用 v-show 切 tab，而
       `.d-flex` 是 display: flex !important，会盖掉 v-show 写进去的 inline
       display: none（见 src/vShowDisplayUtilities.spec.ts）。 -->
  <div class="doc">
    <div v-if="!topic" class="flex-grow-1 d-flex align-center justify-center text-medium-emphasis">
      <div class="text-center">
        <v-icon size="48" class="mb-2 text-disabled">mdi-file-document-outline</v-icon>
        <div>{{ t('work.room.doc.pickTopic') }}</div>
      </div>
    </div>

    <template v-else>
      <!-- Stage: the editor + (optionally) a docked tool panel beside it. -->
      <div class="doc-stage flex-grow-1">
        <div class="doc-tools">
          <DocFormatToolbar
            :editor="surfaceRef?.editor ?? null"
            :disabled="loading || !editable"
            :disabled-reason="loading ? t('work.room.doc.loading') : !editable ? t('work.room.doc.readOnly') : ''"
          />
          <div class="doc-bar">
            <!-- 没连上时说没连上，哪怕正文还没到：那一刻「加载中」会一直挂着。 -->
            <span v-if="loading && connection !== 'offline'" class="t-meta me-2">{{ t('work.room.doc.loading') }}</span>
            <DocPresence v-else :peers="peers" :connection="connection" class="me-2" />

            <!-- 只读是「这一格现在不照常」：开着的时候写在这一条上，点它就回去。没有编辑
               权限时它只是说明，回不去。平常用不上，进去的入口在 ⋯ 里。 -->
            <v-btn
              v-if="!editable"
              size="small"
              variant="text"
              color="medium-emphasis"
              class="me-1"
              :disabled="readOnly"
              :title="readOnly ? t('work.room.doc.noEditAccess') : t('work.room.doc.backToEdit')"
              @click="toggleEditable"
            >
              {{ t('work.room.doc.readOnly') }}
            </v-btn>
            <v-btn
              icon="mdi-comment-text-outline"
              size="small"
              variant="text"
              :aria-label="t('work.room.comments.title')"
              :title="t('work.room.comments.title')"
              :aria-expanded="commentsRef?.opened ?? false"
              @click="commentsRef?.toggle()"
            />
            <v-menu v-if="!readOnly" location="bottom end">
              <template #activator="{ props: menuProps }">
                <v-btn
                  v-bind="menuProps"
                  icon="mdi-dots-horizontal"
                  size="small"
                  variant="text"
                  color="medium-emphasis"
                  :title="t('work.room.menu.more')"
                  :aria-label="t('work.room.menu.more')"
                />
              </template>
              <v-list density="compact" :aria-label="t('work.room.doc.options')">
                <v-list-item
                  :title="editable ? t('work.room.doc.setReadOnly') : t('work.room.doc.backToEdit')"
                  @click="toggleEditable"
                />
              </v-list>
            </v-menu>
          </div>
        </div>
        <!-- Editor surface — a Feishu Docs page: white, padded, centered column. -->
        <DocCommentPanel
          ref="commentsRef"
          v-model:open-id="openId"
          :topic-id="topic?.id ?? null"
          :author="commentAuthor"
          :send-comment="sendComment"
          :thread-state="threadState"
          :thread-actions="threadActions"
          :comments="comments"
          :anchor-nodes="anchorNodes"
          :quote-state="quoteState"
          @locate-node="highlightNode"
          @posted="refreshComments"
        >
          <div
            ref="bodyRef"
            class="doc-body overflow-y-auto"
            :class="{ readonly: !editable }"
            @scroll.passive="onBodyScroll"
          >
            <div class="doc-page" :class="{ 'doc-pulse': pulsing }">
              <!-- Large document title (Feishu Docs), = the topic title -->
              <h1 v-if="!bare" class="doc-page__title">{{ topicTitle(topic) }}</h1>
              <!-- 正文本身。 -->
              <DocSurface
                ref="surfaceRef"
                :editable="editable"
                :loading="loading"
                :session="session"
                :title="topicTitle(topic)"
                :topic-id="topic?.id ?? null"
                :topic-list="topicList"
                :live-ref-index="liveRefIndex"
                :comment-mark-index="commentMarkIndex"
                :open-comment-id="openId"
                :fetch-doc-nodes="fetchDocNodes"
                :image-src="imageSrc"
                :pulse="pulse"
                :scroll-tick="scrollTick"
                :agent-name="agentName"
                :can-rewrite="!!rewriteSelection && editable"
                @open-topic="emit('open-topic', $event)"
                @mention-click="emit('mention-click', $event)"
                @open-file="emit('open-file', $event)"
                @open-comment="openComment"
                @rewrite="rewrite.open($event.from, $event.to)"
                @locate-comment="locateComment"
                @error="setError"
              />

              <DocEditLayer :editor="surfaceRef?.editor ?? null" :agent-name="agentName" :rewrite="rewrite" />

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
.doc-tools {
  display: flex;
  align-items: center;
  flex: 0 0 auto;
  min-width: 0;
  border-bottom: 0.5px solid var(--line);
  background: var(--surface);
}
.doc-bar {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  min-height: 40px;
  padding: 0 6px;
  background: var(--surface);
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
  z-index: 30;
  max-width: min(560px, calc(100% - 32px));
  overflow-wrap: anywhere;
  box-shadow: var(--shadow-2);
}
</style>
