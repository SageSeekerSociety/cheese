<script setup lang="ts">
// 文档那一格的**画**：横条、源码模式、飞书式的一栏正文、底下的评论区与两个对话框。
//
// 它只凭 props 渲染，不认识接口也不认识路由 —— 正文的读写、评论、节点、自动保存的那
// 只时钟都在 composables/usePanelDoc.ts 里（#2143）。正文编辑器本身在
// doc/DocSurface.vue：这里画出它的位置，把取来的东西递下去，把底下发上来的动作接住
// （「换了个值」直接落回组合式函数的 ref，「做了个动作」原样再往上发）。
import type { SendDocComment } from '../../composables/useDocCommentDraft'
import type { Block, Topic } from '../../cx_types'
import type { DocSaveStatus } from '../../lib/docEditState'

import { nextTick, onBeforeUnmount, ref, watch } from 'vue'

import { topicTitle } from '../../lib/topicState'
import CodeEditor from '../CodeEditor.vue'

import DocCommentPanel from './doc/DocCommentPanel.vue'
import DocFormatToolbar from './doc/DocFormatToolbar.vue'
import DocSurface from './doc/DocSurface.vue'
import OverviewAuto from './doc/OverviewAuto.vue'

import { t } from '@/i18n'

const props = withDefaults(
  defineProps<{
    topic: Topic | null
    /** 父层在 AI 动过之后加一：文档那一格据此重读芝士刚写的那一版。 */
    activityTick: number
    /** 项目 AI 队友的名字：文档被它改过时，提示里说的是它。 */
    agentName?: string
    /** 项目话题表：正文里的支线徽章、`<#id>` chip 都靠它认名字与状态。 */
    topicList?: Topic[]
    /** 手机上不提供源码模式，这条决定「只读」和提示里写哪句话。 */
    mdAndUp: boolean
    // ---- 这一篇现在是什么状态 ----
    editable: boolean
    editingBlocked: boolean
    loading: boolean
    saveStatus: DocSaveStatus
    paused: boolean
    pausedHint: string
    errorMsg: string | null
    lossy: boolean
    lossyConfirmOpen: boolean
    sourceMode: boolean
    sourceDraft: string
    // ---- 军规 1 的三种「还没落地」 ----
    pendingEdits: string[]
    hasPendingEdits: boolean
    externalDoc: string | null
    // ---- 评论区 ----
    comments: Block[]
    commentAuthor?: string
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
    save: (force?: boolean) => Promise<void>
    confirmLossySave: () => void
    // 名字不带 on*：`on…` 那种写法在 Vue 里是「事件监听器」，被 props 解析让给 attrs，
    // 于是这一类函数得换个名字才收得到（同一个坑 `save` 那边不会踩到）。
    handleBlur: () => void
    handleDocKeydown: (e: KeyboardEvent) => void
    handleSourceInput: (v: string) => void
    refreshComments: () => Promise<void>
    toggleEditable: () => void
    toggleSourceMode: () => void
    enterSourceMode: () => void
    applyPendingEdits: () => void
    discardPendingEdits: () => void
    viewExternalDoc: () => void
    overwriteWithMine: () => void
    setError: (message: string | null) => void
  }>(),
  { agentName: () => t('work.room.defaultAgentName'), topicList: () => [], commentAuthor: '', sendComment: undefined }
)

const emit = defineEmits<{
  (e: 'open-topic', topicId: string): void
  (e: 'mention-click', handle: string): void
  (e: 'open-file', path: string): void
  // 总览自动区里的一条决策 / 里程碑：去向不在话题里，交给拿着路由的那一层。
  (e: 'open-resource', resource: 'milestone'): void
  /** 有人在正文里改了东西（装配服务端那一版时不算）。 */
  (e: 'edited'): void
  /** 这个确认框里「取消」/ 点外面关掉：状态那一半归组合式函数管。 */
  (e: 'close-lossy-confirm'): void
}>()

function closeLossyConfirm() {
  emit('close-lossy-confirm')
}

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
function openComment(payload: { anchorId: string | null; quote: string }) {
  commentsRef.value?.open(payload)
}
function locateComment(commentId: string) {
  commentsRef.value?.locate(commentId)
}
watch(
  () => [props.topic?.id, props.commentAuthor],
  () => {
    openId.value = null
  }
)
function quoteState(id: string) {
  return surfaceRef.value?.commentQuoteState(id) ?? 'missing'
}

// 组合式函数要的两个口子都长在这一层：它判「这一版和读到的差在哪」，只有这里知道编辑器
// 在哪、怎么装、怎么读回来。
defineExpose({
  pulse,
  highlightTurn,
  installMarkdown: (body: string) => surfaceRef.value?.installMarkdown(body),
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
      <!-- 军规 1 notices. Above the stage so they show in BOTH visual and
           source mode — the states they describe survive a mode switch. -->
      <!-- Edits a mode switch could not carry over: held, not dropped. -->
      <div v-if="hasPendingEdits" class="doc-notice">
        <v-icon size="16" class="doc-notice__icon">mdi-content-save-alert-outline</v-icon>
        <div class="doc-notice__text">
          {{ t('work.room.doc.pendingKept') }}
          <template v-if="pendingEdits.length > 1">{{
            t('work.room.doc.pendingCount', { count: pendingEdits.length })
          }}</template>
        </div>
        <button type="button" class="doc-notice__btn" @click="applyPendingEdits">
          {{ t('work.room.doc.restoreMine') }}
        </button>
        <button type="button" class="doc-notice__btn doc-notice__btn--quiet" @click="discardPendingEdits">
          {{ t('work.room.doc.discard') }}
        </button>
      </div>
      <!-- Server and local both moved: neither side wins silently. -->
      <div v-if="externalDoc !== null" class="doc-notice doc-notice--conflict">
        <v-icon size="16" class="doc-notice__icon">mdi-source-branch</v-icon>
        <div class="doc-notice__text">{{ t('work.room.doc.externalConflict', { name: agentName }) }}</div>
        <button type="button" class="doc-notice__btn" @click="viewExternalDoc">
          {{ t('work.room.doc.viewAgentVersion', { name: agentName }) }}
        </button>
        <button type="button" class="doc-notice__btn" @click="overwriteWithMine">
          {{ t('work.room.doc.keepMine') }}
        </button>
      </div>

      <!-- Stage: the editor + (optionally) a docked tool panel beside it. -->
      <div class="doc-stage flex-grow-1">
        <div class="doc-tools">
          <DocFormatToolbar
            v-if="!sourceMode"
            :editor="surfaceRef?.editor ?? null"
            :disabled="loading || !editable || editingBlocked"
            :disabled-reason="
              loading ? t('work.room.doc.loading') : !editable || editingBlocked ? t('work.room.doc.readOnly') : ''
            "
            @keydown="handleDocKeydown"
          />
          <div class="doc-bar" :class="{ 'doc-bar--row': sourceMode }">
            <span v-if="saveStatus === 'loading'" class="t-meta me-2">{{ t('work.room.doc.loading') }}</span>
            <span v-else-if="saveStatus === 'saving'" class="t-meta me-2">{{ t('work.room.doc.saving') }}</span>
            <!-- 军规 1: autosave is paused — say so instead of faking progress. -->
            <span v-else-if="saveStatus === 'paused'" class="doc-status-paused me-2" :title="pausedHint">
              <v-icon size="13">mdi-pause-circle-outline</v-icon>
              {{ t('work.room.doc.paused') }}
            </span>
            <span v-else-if="saveStatus === 'saved'" class="d-inline-flex align-center ga-1 t-meta me-2">
              <span class="status-dot status-dot--ok" />{{ t('work.room.doc.saved') }}
            </span>
            <span v-else-if="saveStatus === 'dirty'" class="t-meta me-2">{{ t('work.room.doc.editing') }}</span>

            <!-- 只读和源码是两种「这一格现在不照常」的状态：开着的时候写在这一条上，点它
               就回去。平常用不上，进去的入口在 ⋯ 里。 -->
            <v-btn
              v-if="!editable && !editingBlocked"
              size="small"
              variant="text"
              color="medium-emphasis"
              class="me-1"
              :title="t('work.room.doc.backToEdit')"
              @click="toggleEditable"
            >
              {{ t('work.room.doc.readOnly') }}
            </v-btn>
            <v-btn
              v-if="sourceMode"
              size="small"
              variant="text"
              class="me-1 tool-btn--active"
              :title="t('work.room.doc.exitSourceMode')"
              @click="toggleSourceMode"
            >
              {{ t('work.room.doc.source') }}
            </v-btn>
            <v-btn
              v-if="!sourceMode"
              icon="mdi-comment-text-outline"
              size="small"
              variant="text"
              :aria-label="t('work.room.comments.title')"
              :title="t('work.room.comments.title')"
              :aria-expanded="commentsRef?.opened ?? false"
              @click="commentsRef?.toggle()"
            />
            <v-menu v-if="!editingBlocked || mdAndUp" location="bottom end">
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
                  v-if="!editingBlocked"
                  :title="editable ? t('work.room.doc.setReadOnly') : t('work.room.doc.backToEdit')"
                  :disabled="sourceMode"
                  @click="toggleEditable"
                />
                <!-- 源码: raw markdown in Monaco — the lossless escape hatch for any
                   syntax the visual editor can't fully represent (军规 1)。手机上不提供，
                   见 editingBlocked。 -->
                <v-list-item
                  v-if="mdAndUp"
                  :title="sourceMode ? t('work.room.doc.exitSourceMode') : t('work.room.doc.sourceMode')"
                  :subtitle="t('work.room.doc.sourceModeHint')"
                  @click="toggleSourceMode"
                />
              </v-list>
            </v-menu>
          </div>
        </div>
        <!-- 源码模式: the raw markdown file in Monaco. Full-bleed (no page
           column) — this is the file itself, not the document view. -->
        <div v-if="sourceMode" class="doc-source" @keydown="handleDocKeydown">
          <CodeEditor
            :model-value="sourceDraft"
            filename="doc.md"
            :readonly="!editable"
            @update:model-value="handleSourceInput"
            @save="save()"
          />
        </div>
        <!-- Editor surface — a Feishu Docs page: white, padded, centered column. -->
        <DocCommentPanel
          v-else
          ref="commentsRef"
          v-model:open-id="openId"
          :topic-id="topic?.id ?? null"
          :author="commentAuthor"
          :send-comment="sendComment"
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
            @focusout="handleBlur"
            @scroll.passive="onBodyScroll"
          >
            <div class="doc-page" :class="{ 'doc-pulse': pulsing }">
              <!-- Large document title (Feishu Docs), = the topic title -->
              <h1 class="doc-page__title">{{ topicTitle(topic) }}</h1>
              <!-- 军规 1 banner: this doc uses syntax the visual editor can't
               fully represent — autosave is paused, source mode is lossless. -->
              <div v-if="lossy" class="doc-lossy-banner">
                <v-icon size="16" class="doc-lossy-banner__icon">mdi-alert-outline</v-icon>
                <div class="doc-lossy-banner__text">
                  {{ editingBlocked ? t('work.room.doc.lossyBlocked') : t('work.room.doc.lossy') }}
                </div>
                <button v-if="!editingBlocked" type="button" class="doc-lossy-banner__btn" @click="enterSourceMode()">
                  {{ t('work.room.doc.switchToSource') }}
                </button>
              </div>
              <!-- 正文本身。⌘S 从这一层原样落下去（存不存是取数那一半的事）。 -->
              <DocSurface
                ref="surfaceRef"
                :editable="editable"
                :loading="loading"
                :topic-id="topic?.id ?? null"
                :topic-list="topicList"
                :live-ref-index="liveRefIndex"
                :comment-mark-index="commentMarkIndex"
                :open-comment-id="openId"
                :fetch-doc-nodes="fetchDocNodes"
                :image-src="imageSrc"
                :pulse="pulse"
                :scroll-tick="scrollTick"
                @keydown="handleDocKeydown"
                @edited="emit('edited')"
                @open-topic="emit('open-topic', $event)"
                @mention-click="emit('mention-click', $event)"
                @open-file="emit('open-file', $event)"
                @open-comment="openComment"
                @locate-comment="locateComment"
                @error="setError"
              />

              <!-- 总览房间的其余三块（#1889 ②~④）：正文下面、评论区上面。只有根话题
                 有——别的房间的文档就是它自己那一份，没有人从那里看项目全局。 -->
              <OverviewAuto
                v-if="topic?.kind === 'root'"
                :topic="topic"
                :activity-tick="activityTick"
                @open-topic="emit('open-topic', $event)"
                @open-resource="emit('open-resource', $event)"
              />
            </div>
          </div>
        </DocCommentPanel>
      </div>
      <!-- /.doc-stage -->

      <!-- 军规 1: manual save of a lossy-loaded doc needs explicit consent. -->
      <v-dialog :model-value="lossyConfirmOpen" max-width="440" @update:model-value="closeLossyConfirm">
        <v-card rounded="lg">
          <v-card-title class="text-subtitle-1 d-flex align-center ga-2">
            <v-icon size="20" color="warning">mdi-alert-outline</v-icon>
            {{ t('work.room.doc.saveAnywayTitle') }}
          </v-card-title>
          <v-card-text class="text-body-2 pt-0">
            {{ t('work.room.doc.saveAnywayBody') }}
          </v-card-text>
          <v-card-actions>
            <v-spacer />
            <v-btn size="small" variant="text" @click="closeLossyConfirm()">
              {{ t('work.room.doc.cancel') }}
            </v-btn>
            <v-btn size="small" variant="tonal" color="primary" @click="enterSourceMode()">
              {{ t('work.room.doc.editInSource') }}
            </v-btn>
            <v-btn size="small" variant="flat" color="warning" @click="confirmLossySave">
              {{ t('work.room.doc.saveAnyway') }}
            </v-btn>
          </v-card-actions>
        </v-card>
      </v-dialog>

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
.doc-bar--row {
  flex: 1 1 auto;
  justify-content: flex-end;
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
/* The topic title stays outside the editable Markdown. */
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

/* Tool icon when its drawer is open — neutral ink, not amber. */
.tool-btn--active {
  color: var(--ink) !important;
  background: transparent;
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
/* 竖着排：源码模式下工具条是压在编辑器上面的一行。平时它浮着，不占这一列。 */
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

/* ---- 军规 1 UI ---- */
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
/* Lossy-load banner: a warning, so the warn triple — mark, wash, ink. */
.doc-lossy-banner {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  max-width: 720px;
  margin: 0 auto 16px;
  padding: 9px 12px;
  border-radius: 8px;
  border: 1px solid var(--warn);
  background: var(--warn-wash);
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
}
.doc-lossy-banner__icon {
  color: var(--warn);
  margin-top: 2px;
}
.doc-lossy-banner__text {
  flex: 1 1 auto;
  min-width: 0;
}
.doc-lossy-banner__btn {
  flex: 0 0 auto;
  border: 1px solid var(--warn);
  background: var(--surface);
  color: var(--warn-ink);
  border-radius: var(--radius-sm);
  padding: 2px 10px;
  font-size: 13px;
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.doc-lossy-banner__btn:hover {
  background: var(--warn-wash);
}
/* Header status for the paused state — an honest, quiet warning, not the
   「编辑中…」 that used to impersonate a save in progress. */
.doc-status-paused {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--warn);
  cursor: default;
}

/* 军规 1 notices: content held aside (stash) or in conflict. Full-width, above
   the stage, so they follow the user across 可视化 ⇄ 源码. */
.doc-notice {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
  border-bottom: 1px solid var(--line-2);
  background: color-mix(in srgb, var(--warn) 8%, var(--surface));
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
}
.doc-notice--conflict {
  background: color-mix(in srgb, var(--danger) 7%, var(--surface));
}
.doc-notice__icon {
  flex: 0 0 auto;
  color: var(--warn);
}
.doc-notice--conflict .doc-notice__icon {
  color: var(--danger);
}
.doc-notice__text {
  flex: 1 1 auto;
  min-width: 0;
}
.doc-notice__btn {
  flex: 0 0 auto;
  border: 1px solid var(--line-2);
  background: var(--surface);
  color: var(--text);
  border-radius: 6px;
  padding: 2px 10px;
  font-size: 13px;
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.doc-notice__btn:hover {
  background: color-mix(in srgb, var(--text) 6%, var(--surface));
}
.doc-notice__btn--quiet {
  border-color: transparent;
  background: transparent;
  color: var(--muted);
}

/* 源码模式: Monaco fills the stage (it scrolls itself). */
.doc-source {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
}
</style>
