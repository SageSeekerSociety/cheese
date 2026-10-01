<script setup lang="ts">
// Paragraph quote chips locate their document node; whole-document comments have no anchor.
// The host fetches comments once for this list and the editor's underline decorations.
import type { SendDocComment } from '../../../composables/useDocCommentDraft'
import type { Block } from '../../../cx_types'

import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { useDocCommentDraft } from '../../../composables/useDocCommentDraft'
import { isAgentHandle } from '../../../lib/authorship'
import { relTime } from '../../../lib/relTime'
import CheeseAvatar from '../../CheeseAvatar.vue'

import { t } from '@/i18n'

const props = defineProps<{
  topicId: string | null
  author?: string
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
function draftTarget() {
  return target.value ?? { anchorId: null, quote: '' }
}
watch(busy, (value) => emit('busy', value), { immediate: true, flush: 'sync' })
function select(id: string) {
  localOpenId.value = id
  emit('update:openId', id)
}
function cardClick(e: MouseEvent, id: string) {
  if (e.defaultPrevented || !(e.target instanceof Element)) return
  if (e.target.closest('button, a, input, textarea, select, [contenteditable="true"]')) return
  const selection = root.value?.ownerDocument.getSelection()
  if (selection && !selection.isCollapsed && selection.anchorNode && root.value?.contains(selection.anchorNode)) return
  select(id)
}
function cardKey(e: KeyboardEvent, id: string) {
  if (e.target !== e.currentTarget || e.defaultPrevented || e.isComposing || e.repeat) return
  if (e.key !== 'Enter' && e.key !== ' ') return
  e.preventDefault()
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

// A short label for a doc node, used as the anchor-chip fallback when a comment
// has no quoted span. The node's own content is either AI- or human-authored
// text; we only ever truncate it for display (never to derive semantics).
function nodeLabel(content: string): string {
  const label = content.replace(/^#+\s*/, '').trim()
  return label.length > 22 ? label.slice(0, 22) + '…' : label || t('work.room.comments.emptyParagraph')
}
/** The paragraph a comment points at (or null for a whole-doc comment). */
function commentAnchor(c: Block): Block | null {
  return c.reply_to ? props.anchorNodes.find((n) => n.id === c.reply_to) ?? null : null
}

// ---- 写评论 ----
// 批注归批注，聊天归聊天: this input used to be the workspace's shared chat box,
// silently retargeted by selecting text. It lives where the comments are now.
const input = ref<{ focus?: () => void } | null>(null)

function open(target: { anchorId: string | null; quote: string }) {
  folded.value = false
  openDraft(target)
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
      <button
        type="button"
        class="doc-comments__fold"
        :title="folded ? t('work.room.comments.expand') : t('work.room.comments.collapse')"
        @click="folded = !folded"
      >
        <v-icon size="15" class="c-faint">
          {{ folded ? 'mdi-chevron-right' : 'mdi-chevron-down' }}
        </v-icon>
        <v-icon size="15" class="c-faint">mdi-comment-text-outline</v-icon>
        {{ t('work.room.comments.title') }}
        <span v-if="comments.length" class="doc-comments__count">
          {{ comments.length }}
        </span>
      </button>
      <v-spacer />
      <v-btn
        icon="mdi-plus"
        size="x-small"
        variant="text"
        color="on-surface-variant"
        :title="t('work.room.comments.write')"
        @click="open({ anchorId: null, quote: '' })"
      />
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
      <article
        v-for="c in comments"
        :key="c.id"
        class="doc-comments__item"
        :data-comment-card="c.id"
        :class="{ 'is-active': activeId() === c.id }"
        tabindex="0"
        :aria-expanded="activeId() === c.id"
        :aria-label="`${c.author ?? ''} · ${relTime(c.created_at)}`"
        @click="cardClick($event, c.id)"
        @keydown="cardKey($event, c.id)"
      >
        <CheeseAvatar v-if="isAgentHandle(c.author ?? '')" :size="24" :name="c.author ?? ''" :handle="c.author" />
        <span v-else class="doc-comments__avatar">
          {{ (c.author || '?').slice(0, 1).toUpperCase() }}
        </span>
        <div class="doc-comments__main">
          <div class="doc-comments__meta">
            <span class="doc-comments__author">{{ c.author }}</span>
            <span class="t-meta">{{ relTime(c.created_at) }}</span>
          </div>
          <!-- Anchored comment: quoted-span chip → scroll & flash its
             paragraph. A dead anchor — the node id no longer resolves,
             or the node row was deleted and the FK nulled reply_to
             (leaving only the quote) — says so instead of a dead chip. -->
          <button
            v-if="c.reply_to && commentAnchor(c)"
            type="button"
            class="doc-comments__chip"
            :title="t('work.room.comments.locate')"
            dir="auto"
            @click="
              select(c.id)
              emit('locate-node', c.reply_to!)
            "
          >
            {{ c.anchor_quote || nodeLabel(commentAnchor(c)!.content) }}
          </button>
          <div v-else-if="c.reply_to || c.anchor_quote" class="doc-comments__stale">
            {{ t('work.room.comments.anchorChanged') }}
          </div>
          <div
            v-if="c.anchor_quote && quoteStatus(c) !== 'unique' && c.reply_to && commentAnchor(c)"
            class="doc-comments__stale"
          >
            {{
              t(
                quoteStatus(c) === 'ambiguous'
                  ? 'work.room.comments.anchorAmbiguous'
                  : 'work.room.comments.anchorChanged'
              )
            }}
          </div>
          <div
            class="doc-comments__text"
            :class="{ 'is-expanded': expanded.has(c.id) }"
            :data-comment-body="c.id"
            dir="auto"
          >
            {{ c.content }}
          </div>
          <button
            v-if="activeId() === c.id && overflowing.has(c.id)"
            type="button"
            class="doc-comments__resume"
            :aria-expanded="expanded.has(c.id)"
            @click="expandBody(c.id)"
          >
            {{ t(expanded.has(c.id) ? 'work.room.comments.showLess' : 'work.room.comments.showMore') }}
          </button>
        </div>
      </article>
    </template>
  </div>
</template>

<style scoped>
.comment-card--pulse {
  animation: comment-pulse 1.5s ease;
}
@keyframes comment-pulse {
  0% {
    background: rgba(var(--v-theme-primary), 0.16);
  }
  100% {
    background: transparent;
  }
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

/* A2: in-place live-ref badge — a subtopic spawned from this paragraph. It's a
   ProseMirror widget decoration rendered IN the document flow, right after the
   paragraph's last character — no overlay, so it can never block the caret.
   :deep because the widget span is created imperatively by the extension. */
.doc-editor :deep(.doc-liveref) {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  vertical-align: baseline;
  margin-left: 8px;
  max-width: 240px;
  padding: 1px 9px;
  border-radius: var(--radius-lg);
  font-size: 12px;
  line-height: 1.6;
  white-space: nowrap;
  color: rgb(var(--v-theme-primary));
  background: color-mix(in srgb, rgb(var(--v-theme-primary)) 5%, var(--surface));
  border: 1px solid rgba(var(--v-theme-primary), 0.3);
  box-shadow: var(--shadow-1);
  cursor: pointer;
  user-select: none;
  transition:
    background 0.15s,
    box-shadow 0.15s;
}
.doc-editor :deep(.doc-liveref:hover) {
  background: rgba(var(--v-theme-primary), 0.1);
  box-shadow: var(--shadow-2);
}
.doc-editor :deep(.doc-liveref__icon) {
  flex: 0 0 auto;
  font-size: 13px;
  line-height: 1;
}
.doc-editor :deep(.doc-liveref__label) {
  overflow: hidden;
  text-overflow: ellipsis;
}
.doc-editor :deep(.doc-liveref__dot) {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex: 0 0 auto;
  background: var(--warn); /* 进行中 */
}
.doc-editor :deep(.doc-liveref__dot.is-archived),
.doc-editor :deep(.doc-liveref__dot.is-completed) {
  background: var(--ok); /* 已完成 */
}
.doc-editor :deep(.doc-liveref__status) {
  color: var(--muted);
  font-size: 12px;
}

/* 飞书 docs 风常驻评论区 at the bottom of the document column. */
.doc-comments {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  margin: 12px 0 0;
}
/* 写评论的输入框，长在评论区里。区块靠留白和一层浅底分出来，不用卡片也不用左条纹。 */
.comment-draft {
  margin: 8px 0 12px;
  padding: 8px 10px;
  border-radius: var(--radius-md);
  background: var(--fill);
}
.comment-draft__quote {
  display: flex;
  align-items: flex-start;
  gap: 4px;
  margin-bottom: 6px;
  font-size: 13px;
  color: var(--muted);
}
.comment-draft__error {
  margin-bottom: 4px;
  font-size: 12px;
  color: var(--danger-ink);
}
.comment-draft__input :deep(textarea) {
  font-size: 14px;
  line-height: 1.6;
}
.doc-comments__head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
  color: var(--muted);
  margin-bottom: 12px;
}
.doc-comments__count {
  font-size: 12px;
  font-weight: 600;
  padding: 0 6px;
  border-radius: 8px;
  color: var(--muted);
  background: var(--fill);
}
.doc-comments__item {
  display: flex;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid transparent;
  border-radius: var(--radius-lg);
  background: var(--surface);
  margin-bottom: 8px;
}
.doc-comments__avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  flex: 0 0 auto;
  font-size: 0.7rem;
  font-weight: 700;
  color: var(--muted);
  background: var(--fill);
}
.doc-comments__main {
  flex: 1 1 auto;
  min-width: 0;
}
.doc-comments__meta {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 1px;
}
.doc-comments__author {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
}
.doc-comments__text {
  font-size: 14px;
  line-height: 1.6;
  color: var(--text);
  white-space: pre-wrap;
  word-break: break-word;
}
/* Anchored comment's quote chip: the message-quote visual language (a neutral
   left bar over the fill ground). Click → scroll + flash the paragraph. */
.doc-comments__chip {
  display: block;
  max-width: 100%;
  text-align: start;
  border: none;
  border-inline-start: 2px solid var(--line-2);
  background: var(--fill);
  border-top-right-radius: var(--radius-sm);
  border-bottom-right-radius: var(--radius-sm);
  padding: 3px 8px;
  margin: 2px 0 4px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--muted);
  cursor: pointer;
  overflow: hidden;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  white-space: pre-wrap;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.doc-comments__chip:hover {
  background: rgba(var(--v-theme-primary), 0.13);
}
/* The anchor node no longer exists — the paragraph was edited away. */
.doc-comments__item {
  cursor: pointer;
}
.doc-comments__item:hover {
  background: var(--fill);
}
.doc-comments__item.is-active {
  border-color: var(--line-2);
  background: var(--fill);
}
.doc-comments__item:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: -2px;
}
.doc-comments__text {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow: hidden;
}
.is-active .doc-comments__text {
  display: block;
  max-height: 25.6em;
  -webkit-line-clamp: unset;
}
.is-active .doc-comments__text.is-expanded {
  max-height: none;
}
.doc-comments__resume {
  margin: 4px 0;
  color: var(--accent-ink);
  font-size: 12px;
  cursor: pointer;
}
.comment-draft__quote {
  border-inline-start: 2px solid var(--line-2);
  padding-inline-start: 8px;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow: hidden;
}
.doc-comments__stale {
  font-size: 12px;
  color: var(--faint);
  margin: 2px 0 4px;
}
.doc-comments__composer {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
}
.doc-comments__input {
  flex: 1 1 auto;
  min-width: 0;
  height: 34px;
  padding: 0 12px;
  border: 1px solid var(--line-2);
  border-radius: 8px;
  background: var(--fill);
  font-size: 14px;
  color: var(--text);
  outline: none;
  transition:
    border-color 0.15s,
    background 0.15s;
}
.doc-comments__input:focus {
  border-color: rgba(var(--v-theme-primary), 0.5);
  background: var(--surface);
}
.doc-comments__input::placeholder {
  color: var(--faint);
}
</style>
