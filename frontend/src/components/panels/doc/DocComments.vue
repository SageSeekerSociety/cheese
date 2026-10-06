<script setup lang="ts">
// 评论栏里的内容：写评论的框（从选中的字开出来，或者对整篇），和一串串评论，没解决的
// 和已解决的分开看。每一串一张卡（DocThreadCard），对话直接摊开，不用点进去。
//
// 评论串从上面递进来（useDocThreads），写也交给上面；这一层只管草稿、筛选、在看哪一串。
import type { SendDocComment } from '../../../composables/useDocCommentDraft'
import type { CommentSpot } from '../../../lib/docCommentSpots'
import type { DocThreadActions, DocThreadState, ThreadPlace } from '../../../lib/docThreadTypes'

import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

import { scrollBehavior } from '@/utils/motion'

import { useDocCommentDraft } from '../../../composables/useDocCommentDraft'

import DocThreadCard from './DocThreadCard.vue'

import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import { t } from '@/i18n'

const props = defineProps<{
  topicId: string | null
  author: string
  threadState: DocThreadState
  threadActions: DocThreadActions
  sendComment?: SendDocComment
  openId: string | null
  /** 这一串评的那几个字在正文里的哪。 */
  placeOf: (id: string) => ThreadPlace
  agentName: string
  mentionNames: Record<string, string>
  nameOf: (handle: string) => string
  /** handle 读成他挑过的头像地址；他没挑过、或不在名册上时给空串，画首字母。 */
  avatarOf?: (handle: string) => string
  /** 能写（话题没归档）。 */
  writable: boolean
  /** 看没解决的，还是已解决的。 */
  filter: 'open' | 'resolved'
}>()
const emit = defineEmits<{
  (e: 'update:openId', id: string | null): void
  /** 正文滚到这一串评的那几个字。 */
  (e: 'locate', id: string): void
  (e: 'busy', busy: boolean): void
}>()

const {
  draft,
  hasDraft,
  busy,
  close,
  text,
  sending,
  errorMsg,
  open: openDraft,
  cancel,
  submit,
  target,
} = useDocCommentDraft(
  () => props.topicId,
  () => props.author,
  (...args) =>
    props.sendComment ? props.sendComment(...args) : Promise.reject(new Error(t('work.room.comments.unavailable'))),
  () => {}
)
watch(busy, (value) => emit('busy', value), { immediate: true, flush: 'sync' })

const threads = computed(() => props.threadState.threads.filter((thread) => thread.state === props.filter))

const root = ref<HTMLElement | null>(null)
const input = ref<HTMLTextAreaElement | null>(null)
let disposed = false
onBeforeUnmount(() => {
  disposed = true
})

function grow() {
  const el = input.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, 200)}px`
}
watch(text, () => void nextTick(grow))

/** 开一段评论草稿；`prefill` 是新草稿先写上的字。 */
function open(spot: CommentSpot, prefill?: string) {
  openDraft(spot, prefill)
  const topic = props.topicId
  void nextTick(() => {
    if (!disposed && topic === props.topicId) {
      input.value?.focus()
      grow()
    }
  })
}
function onKey(e: KeyboardEvent) {
  if (e.isComposing || e.keyCode === 229) return
  if (e.key === 'Escape') {
    // 收起来，草稿留着。
    e.preventDefault()
    e.stopPropagation()
    close()
    return
  }
  if (e.key !== 'Enter' || e.shiftKey) return
  e.preventDefault()
  void submit()
}
function resume() {
  if (target.value) open(target.value)
}

function select(id: string) {
  emit('update:openId', id)
  emit('locate', id)
}
/** 正文里点了这一串的字：卡片翻出来、滚到眼前。 */
function locate(id: string) {
  emit('update:openId', id)
  const topic = props.topicId
  void nextTick(() => {
    if (disposed || topic !== props.topicId) return
    root.value
      ?.querySelector(`[data-thread="${CSS.escape(id)}"]`)
      ?.scrollIntoView({ behavior: scrollBehavior(), block: 'nearest' })
  })
}

// ---- 写：交给上面，回执到了清掉自己的回复框 ----
const cards = ref<Record<string, InstanceType<typeof DocThreadCard> | null>>({})
async function reply(id: string, body: string) {
  try {
    await props.threadActions.reply(id, body)
    cards.value[id]?.sent(body)
  } catch {
    // 错误写在那张卡上（threadState.errors）。
  }
}
async function act(id: string, action: 'resolve' | 'reopen') {
  try {
    await props.threadActions[action](id)
  } catch {
    // 同上。
  }
}
async function stopAgent(id: string) {
  try {
    await props.threadActions.stopAgent(id)
  } catch {
    // 同上。
  }
}
async function resend(id: string) {
  try {
    const sent = await props.threadActions.recover(id)
    if (sent) cards.value[id]?.sent(sent.reply)
  } catch {
    // 同上。
  }
}

defineExpose({ open, locate })
</script>

<template>
  <div ref="root" class="doc-comments">
    <div v-if="draft" class="doc-comments__draft">
      <div v-if="draft.quote" class="doc-comments__quote" dir="auto">{{ draft.quote }}</div>
      <textarea
        ref="input"
        v-model="text"
        rows="2"
        autocomplete="off"
        class="doc-comments__input"
        :aria-label="t('work.room.comments.write')"
        :placeholder="t('work.room.comments.placeholder')"
        :title="t('work.room.comments.keyHint')"
        @keydown="onKey"
      />
      <p v-if="errorMsg" class="doc-comments__error" role="alert">{{ errorMsg }}</p>
      <div class="doc-comments__draft-actions">
        <button type="button" class="doc-comments__button" :disabled="sending" @click="cancel">
          {{ t('work.room.comments.cancel') }}
        </button>
        <button
          type="button"
          class="doc-comments__button doc-comments__button--primary"
          :disabled="sending || !text.trim() || !sendComment"
          @click="submit"
        >
          {{ t('work.room.comments.comment') }}
        </button>
      </div>
    </div>
    <button v-else-if="hasDraft" type="button" class="doc-comments__resume" @click="resume">
      {{ t('work.room.comments.resumeDraft') }}
    </button>

    <TransitionGroup tag="div" name="doc-comments-card" class="doc-comments__list">
      <DocThreadCard
        v-for="thread in threads"
        :key="thread.comment.id"
        :ref="(card) => (cards[thread.comment.id] = card as InstanceType<typeof DocThreadCard> | null)"
        :thread="thread"
        :active="openId === thread.comment.id"
        :place="placeOf(thread.comment.id)"
        :activity="threadState.activity[thread.comment.id]"
        :busy="threadState.busy"
        :unknown="threadState.unknown === thread.comment.id"
        :error="threadState.errors[thread.comment.id]"
        :agent-name="agentName"
        :mention-names="mentionNames"
        :name-of="nameOf"
        :avatar-of="avatarOf"
        :writable="writable"
        :draft-key="`cheese.doc-thread.draft.v1:${author}:${topicId}:${thread.comment.id}`"
        @select="select(thread.comment.id)"
        @locate="select(thread.comment.id)"
        @reply="reply(thread.comment.id, $event)"
        @resolve="act(thread.comment.id, 'resolve')"
        @reopen="act(thread.comment.id, 'reopen')"
        @resend="resend(thread.comment.id)"
        @stop-agent="stopAgent(thread.comment.id)"
      />
    </TransitionGroup>
    <BaseEmptyState
      v-if="!threads.length && !draft"
      size="inline"
      align="center"
      class="doc-comments__empty"
      :title="filter === 'open' ? t('work.room.comments.emptyOpen') : t('work.room.comments.emptyResolved')"
    />
  </div>
</template>

<style scoped>
.doc-comments {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 12px;
}
.doc-comments__list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.doc-comments__draft {
  padding: 12px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  background: var(--surface);
}
.doc-comments__quote {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow: hidden;
  margin-bottom: 8px;
  padding-left: 8px;
  border-left: 2px solid var(--accent);
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}
.doc-comments__input {
  display: block;
  width: 100%;
  border: 0;
  outline: 0;
  resize: none;
  background: transparent;
  color: var(--ink);
  font: inherit;
  font-size: 14px;
  line-height: var(--lh-14);
}
.doc-comments__error {
  margin: 8px 0 0;
  color: var(--danger-ink);
  font-size: 13px;
  line-height: var(--lh-13);
}
.doc-comments__draft-actions {
  display: flex;
  justify-content: flex-end;
  gap: 4px;
  margin-top: 8px;
}
.doc-comments__button {
  height: 28px;
  padding: 0 10px;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--muted);
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}
.doc-comments__button:hover:not(:disabled) {
  background: var(--fill);
  color: var(--text);
}
.doc-comments__button--primary {
  color: var(--accent-ink);
}
.doc-comments__button:disabled {
  color: var(--faint);
  cursor: default;
}
.doc-comments__resume {
  align-self: flex-start;
  padding: 4px 0;
  border: 0;
  background: transparent;
  color: var(--muted);
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}
.doc-comments__resume:hover {
  color: var(--text);
}
.doc-comments__empty {
  margin: 24px 0;
}

/* 输入框自己没有框：外面那张卡就是它的框。 */
.doc-comments button:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}
.doc-comments__draft:focus-within {
  border-color: var(--muted);
}
.doc-comments-card-enter-active {
  transition:
    opacity var(--dur-base) var(--ease-out),
    transform var(--dur-base) var(--ease-out);
}
.doc-comments-card-leave-active {
  transition: opacity var(--dur-quick) var(--ease-in);
}
.doc-comments-card-move {
  transition: transform var(--dur-base) var(--ease-standard);
}
.doc-comments-card-enter-from {
  opacity: 0;
  transform: translateY(-4px);
}
.doc-comments-card-leave-to {
  opacity: 0;
}
</style>
