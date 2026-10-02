<script setup lang="ts">
// Paragraph quote chips locate their document node; whole-document comments have no anchor.
// The host fetches comments once for this list and the editor's underline decorations.
import type { SendDocComment } from '../../../composables/useDocCommentDraft'
import type { Block } from '../../../cx_types'
import type { DocThreadActions, DocThreadState } from '../../../lib/docThreadTypes'

import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { useDocCommentDraft } from '../../../composables/useDocCommentDraft'
import { isAgentHandle } from '../../../lib/authorship'
import { relTime } from '../../../lib/relTime'
import CheeseAvatar from '../../CheeseAvatar.vue'

import DocCommentBody from './DocCommentBody.vue'
import DocThreadView from './DocThreadView.vue'

import { t } from '@/i18n'

const props = defineProps<{
  topicId: string | null
  author?: string
  threadState?: DocThreadState
  threadActions?: DocThreadActions
  sendComment?: SendDocComment
  comments: Block[]
  /** The doc's paragraphs, so an anchored comment can name the one it points at. */
  anchorNodes: Block[]
  openId?: string | null
  quoteState?: (id: string) => 'unique' | 'missing' | 'ambiguous'
}>()

const emit = defineEmits<{
  /** A quote chip was clicked: scroll to and flash that paragraph. */
  (e: 'locate-node', nodeId: string): void
  /** A comment was posted — the host re-fetches (it also feeds the underlines). */
  (e: 'posted'): void
  (e: 'update:openId', id: string | null): void
  (e: 'busy', busy: boolean): void
}>()

const {
  draft,
  hasDraft,
  target,
  busy,
  close,
  text,
  sending,
  errorMsg,
  open: openDraft,
  cancel,
  submit,
} = useDocCommentDraft(
  () => props.topicId,
  () => props.author ?? '',
  (...args) =>
    props.sendComment ? props.sendComment(...args) : Promise.reject(new Error(t('work.room.comments.unavailable'))),
  () => emit('posted')
)

// 页级评论折叠态 (Feishu-style, collapsed head keeps the doc quiet).
const folded = ref(false)
const root = ref<HTMLElement | null>(null)
const localOpenId = ref<string | null>(null)
const expanded = ref(new Set<string>())
const overflowing = ref(new Set<string>())
let observer: ResizeObserver | null = null
let disposed = false
const activeId = () => (props.openId === undefined ? localOpenId.value : props.openId)
const hasActiveThread = computed(
  () => !!props.threadState && !!props.threadActions && props.comments.some((comment) => comment.id === activeId())
)
function draftTarget() {
  return target.value ?? { anchorId: null, quote: '' }
}
watch(busy, (value) => emit('busy', value), { immediate: true, flush: 'sync' })
function select(id: string | null) {
  localOpenId.value = id
  emit('update:openId', id)
}
function backToList() {
  const id = activeId(),
    topic = props.topicId,
    author = props.author
  select(null)
  void nextTick(() => {
    if (disposed || topic !== props.topicId || author !== props.author || hasActiveThread.value) return
    const card = Array.from(root.value?.querySelectorAll<HTMLElement>('[data-comment-card]') ?? []).find(
      (el) => el.dataset.commentCard === id
    )
    card?.querySelector<HTMLButtonElement>('.doc-comments__summary')?.focus({ preventScroll: true })
  })
}
function locateAnchor(comment: Block) {
  if (!comment.reply_to || !commentAnchor(comment)) return
  select(comment.id)
  emit('locate-node', comment.reply_to)
}
function cardClick(e: MouseEvent, id: string) {
  if (e.defaultPrevented || !(e.target instanceof Element)) return
  const control = e.target.closest('button, a, input, textarea, select, [contenteditable="true"]')
  if (control && control !== e.currentTarget) return
  const selection = root.value?.ownerDocument.getSelection()
  if (selection && !selection.isCollapsed && selection.anchorNode && root.value?.contains(selection.anchorNode)) return
  select(id)
}
function expandBody(id: string) {
  const next = new Set(expanded.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  expanded.value = next
}
function measureBodies() {
  const next = new Set<string>()
  root.value?.querySelectorAll<HTMLElement>('[data-comment-body]').forEach((el) => {
    const line = Number.parseFloat(getComputedStyle(el).lineHeight) || 22.4
    if (el.scrollHeight > line * 16 + 1) next.add(el.dataset.commentBody!)
  })
  overflowing.value = next
}
async function observeBodies() {
  await nextTick()
  if (disposed) return
  observer?.disconnect()
  root.value?.querySelectorAll<HTMLElement>('[data-comment-body]').forEach((el) => observer?.observe(el))
  measureBodies()
}
onMounted(() => {
  if (typeof ResizeObserver !== 'undefined') observer = new ResizeObserver(measureBodies)
  void observeBodies()
})
watch(
  () => [props.comments, props.openId, localOpenId.value, folded.value],
  () => void observeBodies(),
  { deep: true }
)
watch(
  () => [props.topicId, props.author],
  () => {
    localOpenId.value = null
    expanded.value = new Set()
  }
)
onBeforeUnmount(() => {
  disposed = true
  observer?.disconnect()
})
function quoteStatus(c: Block) {
  if (!c.reply_to || !commentAnchor(c)) return 'missing'
  return c.anchor_quote ? props.quoteState?.(c.id) ?? 'missing' : 'unique'
}

/** The paragraph a comment points at (or null for a whole-doc comment). */
function commentAnchor(c: Block): Block | null {
  return c.reply_to ? props.anchorNodes.find((n) => n.id === c.reply_to) ?? null : null
}

// ---- 写评论 ----
// 批注归批注，聊天归聊天: this input used to be the workspace's shared chat box,
// silently retargeted by selecting text. It lives where the comments are now.
const input = ref<{ focus?: () => void } | null>(null)

/** `prefill`：新开的评论先写上的字（「问…」时是 @ AI 队友）。 */
function open(target: { anchorId: string | null; quote: string }, prefill?: string) {
  folded.value = false
  openDraft(target, prefill)
  const topic = props.topicId,
    author = props.author
  void nextTick(() => {
    if (!disposed && topic === props.topicId && author === props.author) input.value?.focus?.()
  })
}

function onKey(e: KeyboardEvent) {
  if (e.isComposing || e.keyCode === 229) return
  if (e.key === 'Escape') {
    e.preventDefault()
    e.stopPropagation()
    close()
    return
  }
  if (e.key !== 'Enter' || e.shiftKey) return
  e.preventDefault()
  void submit()
}

// 下划线点回来的那一跳：卡片在这个组件的 DOM 里，所以展开和高亮也归这里。
function locate(commentId: string) {
  folded.value = false
  select(commentId)
  const topic = props.topicId
  void nextTick(() => {
    if (disposed || topic !== props.topicId) return
    const card = Array.from(root.value?.querySelectorAll<HTMLElement>('[data-comment-card]') ?? []).find(
      (el) => el.dataset.commentCard === commentId
    )
    card?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  })
}

defineExpose({ open, locate })
</script>

<template>
  <div ref="root" class="doc-comments">
    <!-- Collapsible head; ONE 写评论 action, and it opens the input
       that lives right here — 批注归批注，聊天归聊天. -->
    <div class="doc-comments__head">
      <button v-if="hasActiveThread" type="button" class="doc-comments__fold" @click="backToList">
        <v-icon size="16">mdi-arrow-left</v-icon>
        {{ t('work.room.comments.all') }}
      </button>
      <button
        v-else
        type="button"
        class="doc-comments__fold"
        :title="folded ? t('work.room.comments.expand') : t('work.room.comments.collapse')"
        @click="folded = !folded"
      >
        <v-icon size="15" class="c-faint">
          {{ folded ? 'mdi-chevron-right' : 'mdi-chevron-down' }}
        </v-icon>
        <v-icon size="15" class="c-faint">mdi-comment-text-outline</v-icon>
        <span class="doc-comments__head-label">{{
          t(folded ? 'work.room.comments.expand' : 'work.room.comments.collapse')
        }}</span>
      </button>
      <v-btn
        prepend-icon="mdi-plus"
        size="small"
        variant="text"
        color="on-surface-variant"
        :title="t('work.room.comments.write')"
        @click="open({ anchorId: null, quote: '' })"
        >{{ t('work.room.comments.write') }}</v-btn
      >
    </div>
    <template v-if="!folded">
      <!-- 写评论: anchored to a paragraph when it came from a
         selection, page-level when it came from the ＋. -->
      <div v-if="draft" class="comment-draft">
        <div v-if="draft.quote" class="comment-draft__quote" dir="auto">
          <v-icon size="13" class="c-faint">mdi-format-quote-close</v-icon>
          {{ draft.quote }}
        </div>
        <v-textarea
          ref="input"
          v-model="text"
          autocomplete="off"
          variant="plain"
          rows="2"
          auto-grow
          max-rows="6"
          hide-details
          density="compact"
          autofocus
          class="comment-draft__input"
          :placeholder="t('work.room.comments.placeholder')"
          :title="t('work.room.comments.keyHint')"
          @keydown="onKey"
        />
        <div v-if="errorMsg" class="comment-draft__error">{{ errorMsg }}</div>
        <div class="d-flex align-center ga-2 justify-end">
          <v-btn
            size="small"
            variant="text"
            :disabled="busy"
            :title="busy ? t('work.room.comments.waitForSend') : ''"
            @click="close"
          >
            {{ t('work.room.comments.closeDraft') }}
          </v-btn>
          <v-btn
            size="small"
            variant="text"
            :disabled="busy"
            :title="busy ? t('work.room.comments.waitForSend') : ''"
            @click="cancel"
          >
            {{ t('work.room.comments.discardDraft') }}
          </v-btn>
          <v-btn
            size="small"
            color="primary"
            variant="flat"
            :loading="sending"
            :disabled="sending || !text.trim() || !sendComment"
            :title="!sendComment ? t('work.room.comments.unavailable') : ''"
            @click="submit"
          >
            {{ t('work.room.comments.comment') }}
          </v-btn>
        </div>
      </div>
      <button
        v-if="!draft && hasDraft"
        type="button"
        class="doc-comments__resume"
        @click="openDraft({ ...draftTarget() })"
      >
        {{ t('work.room.comments.resumeDraft') }}
      </button>
      <div class="doc-comments__list" :class="{ 'has-active-thread': hasActiveThread }">
        <article
          v-for="c in comments"
          v-show="!hasActiveThread || activeId() === c.id"
          :key="c.id"
          class="doc-comments__item"
          :data-comment-card="c.id"
          :class="{ 'is-active': activeId() === c.id }"
        >
          <button
            type="button"
            class="doc-comments__summary"
            :aria-pressed="activeId() === c.id"
            :aria-expanded="activeId() === c.id"
            :aria-label="`${c.author ?? ''} · ${relTime(c.created_at)}`"
            @click="cardClick($event, c.id)"
          >
            <CheeseAvatar v-if="isAgentHandle(c.author ?? '')" :size="24" :name="c.author ?? ''" :handle="c.author" />
            <span v-else class="doc-comments__avatar">
              {{ (c.author || '?').slice(0, 1).toUpperCase() }}
            </span>
            <span class="doc-comments__summary-main">
              <div class="doc-comments__meta">
                <span class="doc-comments__author">{{ c.author }}</span>
                <span class="t-meta">{{ relTime(c.created_at) }}</span>
              </div>
              <span v-if="activeId() !== c.id && c.anchor_quote" class="doc-comments__summary-quote" dir="auto">{{
                c.anchor_quote
              }}</span>
              <span v-if="activeId() !== c.id" class="doc-comments__text" :data-comment-body="c.id" dir="auto">{{
                c.content
              }}</span>
            </span>
            <v-icon v-if="activeId() !== c.id" size="16" class="doc-comments__chevron">mdi-chevron-right</v-icon>
          </button>
          <div v-show="activeId() === c.id" class="doc-comments__main">
            <DocThreadView
              v-if="activeId() === c.id && threadState && threadActions"
              :id="c.id"
              :topic="topicId"
              :actor="author ?? ''"
              :state="threadState"
              :actions="threadActions"
            >
              <DocCommentBody
                :comment="c"
                :anchor="commentAnchor(c)"
                :quote-status="quoteStatus(c)"
                :expanded="expanded.has(c.id)"
                :overflowing="overflowing.has(c.id)"
                @locate="locateAnchor(c)"
                @expand="expandBody(c.id)"
              />
            </DocThreadView>
            <DocCommentBody
              v-else-if="activeId() === c.id"
              :comment="c"
              :anchor="commentAnchor(c)"
              :quote-status="quoteStatus(c)"
              :expanded="expanded.has(c.id)"
              :overflowing="overflowing.has(c.id)"
              @locate="locateAnchor(c)"
              @expand="expandBody(c.id)"
            />
          </div>
        </article>
      </div>
    </template>
  </div>
</template>

<style scoped>
.doc-comments {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-height: 0;
  overflow: hidden;
  padding: 12px 8px;
}
.doc-comments__list {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
}
.doc-comments__list.has-active-thread {
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.has-active-thread .doc-comments__item.is-active {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-height: 0;
}
.has-active-thread .doc-comments__main {
  display: flex;
  flex: 1 1 auto;
  min-height: 0;
  overflow: hidden;
}
.doc-comments__head {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-direction: row-reverse;
  padding: 0 4px 12px;
  gap: 8px;
}
.doc-comments__fold {
  display: flex;
  align-items: center;
  gap: 4px;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}
.doc-comments__head-label {
  display: none;
}
.comment-draft {
  flex: 0 0 auto;
  margin-bottom: 16px;
  padding: 12px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
}
.comment-draft__quote {
  border-inline-start: 2px solid var(--line-2);
  padding-inline-start: 8px;
  margin-bottom: 8px;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow: hidden;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}
.comment-draft__input :deep(textarea) {
  font-size: 14px;
  line-height: var(--lh-14);
}
.comment-draft__error {
  color: var(--danger-ink);
  font-size: 13px;
  line-height: var(--lh-13);
  margin-bottom: 8px;
}
.doc-comments__item {
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  margin-bottom: 8px;
}
.doc-comments__item.is-active {
  border-color: var(--line);
}
.doc-comments__summary {
  display: flex;
  gap: 8px;
  padding: 12px;
  width: 100%;
  text-align: start;
  align-items: flex-start;
  border-radius: var(--radius-md);
}
.doc-comments__summary:hover {
  background: var(--fill);
}
.doc-comments__summary:focus-visible,
.doc-comments__fold:focus-visible,
.doc-comments__resume:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: -2px;
}
.doc-comments__summary-main {
  flex: 1 1 auto;
  min-width: 0;
}
.doc-comments__avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  flex: 0 0 auto;
  border-radius: var(--radius-pill);
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  background: var(--fill);
}
.doc-comments__meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 4px;
}
.doc-comments__author {
  font-size: 13px;
  line-height: var(--lh-13);
  font-weight: 600;
  color: var(--ink);
  overflow-wrap: anywhere;
}
.doc-comments__chevron {
  flex: 0 0 auto;
  color: var(--muted);
}
.doc-comments__summary-quote {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  margin-bottom: 4px;
}
.doc-comments__text {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow: hidden;
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--text);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.doc-comments__main {
  min-width: 0;
  padding: 0 12px 12px;
}
.doc-comments__resume {
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  margin-block: 8px;
  padding: 4px;
}
</style>
