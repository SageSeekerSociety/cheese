<script setup lang="ts">
// 一条评论串一张卡：上面是评的那几个字，下面是来回的每一句；AI 队友在答时说它到了哪一步。
// 点卡片就是在看它（正文滚到那几个字）；在看的那张才给回复框和解决。
import type { DocThread, DocThreadActivity, ThreadPlace } from '../../../lib/docThreadTypes'

import { computed, nextTick, ref, watch } from 'vue'

import { isAgentHandle } from '../../../lib/authorship'
import { plainRefs } from '../../../lib/refChip'
import { relTime } from '../../../lib/relTime'
import { avatarColor, avatarInitial } from '../../../utils/avatar'
import CheeseAvatar from '../../CheeseAvatar.vue'
import MarkdownView from '../../common/MarkdownView.vue'

import { t } from '@/i18n'

const props = defineProps<{
  thread: DocThread
  /** 正在看的那一串。 */
  active: boolean
  /** 评的那几个字在正文里的哪：还在、改写或删掉了但记得原来的位置、无从找起。 */
  place: ThreadPlace
  activity?: DocThreadActivity
  /** 有一次写还没回来：这时不再接新的。 */
  busy: boolean
  /** 这一串上一次写的结果不知道。 */
  unknown: boolean
  error?: string
  agentName: string
  mentionNames: Record<string, string>
  /** handle 读成名字。 */
  nameOf: (handle: string) => string
  /** 能写（话题没归档）。 */
  writable: boolean
  /** 回复框里写到一半的字存在哪（localStorage）：收起、换话题、刷新都还在。 */
  draftKey: string
}>()
const emit = defineEmits<{
  (e: 'select'): void
  (e: 'locate'): void
  (e: 'reply', text: string): void
  (e: 'resolve'): void
  (e: 'reopen'): void
  (e: 'resend'): void
  (e: 'stopAgent'): void
}>()

/** 回复超过这么多条，中间的先收起来。 */
const SHOWN = 3
const expanded = ref(false)
const messages = computed(() => [props.thread.comment, ...props.thread.replies.map((r) => r.comment)])
const hidden = computed(() =>
  expanded.value || props.thread.replies.length <= SHOWN ? 0 : props.thread.replies.length - (SHOWN - 1)
)
const shown = computed(() =>
  hidden.value ? [messages.value[0], ...messages.value.slice(1 + hidden.value)] : messages.value
)
const resolved = computed(() => props.thread.state === 'resolved')

const names = computed(() => ({ mentionNames: props.mentionNames, topicTitles: {} }))

// 芝士正在用的工具，说成它在做的事；没列出的就说在回答
const TOOL_STEPS: Record<string, string> = {
  cheese_doc_get: 'reading',
  cheese_doc_edit: 'editing',
  read: 'readingCode',
  ls: 'readingCode',
  find: 'readingCode',
  grep: 'readingCode',
  git: 'readingCode',
  cheese_project_search: 'searching',
  cheese_memory_read: 'readingMemory',
  cheese_attachment_read: 'readingAttachment',
}

const step = computed(() => {
  const a = props.activity
  if (!a) return null
  if (a.state === 'queued') return t('work.room.docAgent.queued', { agent: props.agentName })
  const doing = a.tool ? TOOL_STEPS[a.tool] : undefined
  return t(doing ? `work.room.comments.${doing}` : 'work.room.docAgent.answering', { agent: props.agentName })
})

// ---- 回复框：在看这张时才有，随字长高；回车发出去，Shift+回车换行 ----
function stored(key: string): string {
  try {
    return localStorage.getItem(key) ?? ''
  } catch {
    return ''
  }
}
const text = ref(stored(props.draftKey))
watch(
  () => props.draftKey,
  (key) => (text.value = stored(key))
)
watch(text, (value) => {
  try {
    if (value) localStorage.setItem(props.draftKey, value)
    else localStorage.removeItem(props.draftKey)
  } catch {
    // 存不下就只留在这一页上。
  }
})
const input = ref<HTMLTextAreaElement | null>(null)
function grow() {
  const el = input.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, 160)}px`
}
watch(text, () => void nextTick(grow))
function onKey(e: KeyboardEvent) {
  if (e.isComposing || e.keyCode === 229 || e.key !== 'Enter' || e.shiftKey) return
  e.preventDefault()
  send()
}
function send() {
  const body = text.value.trim()
  if (!body || props.busy) return
  emit('reply', body)
}
/** 发出去了：框清空。上面那一层在回执到了之后叫它。 */
function sent(body: string) {
  if (text.value.trim() === body) text.value = ''
}
defineExpose({ sent })

function onCard(e: MouseEvent) {
  if (!(e.target instanceof Element) || e.target.closest('button, a, textarea, input')) return
  const selection = window.getSelection()
  if (selection && !selection.isCollapsed) return
  emit('select')
}
</script>

<template>
  <article
    class="doc-thread-card"
    :class="{ 'is-active': active, 'is-resolved': resolved }"
    :data-thread="thread.comment.id"
    @click="onCard"
  >
    <button
      v-if="thread.comment.anchor_quote"
      type="button"
      class="doc-thread-card__quote"
      :class="{ 'is-gone': place !== 'marked' }"
      :disabled="!place"
      :title="place === 'placed' ? t('work.room.comments.quoteChanged') : undefined"
      dir="auto"
      @click="emit('locate')"
    >
      {{ thread.comment.anchor_quote }}
    </button>

    <div v-for="(message, i) in shown" :key="message.id" class="doc-thread-card__message">
      <button v-if="hidden && i === 1" type="button" class="doc-thread-card__more" @click="expanded = true">
        {{ t('work.room.comments.moreReplies', { n: hidden }) }}
      </button>
      <div class="doc-thread-card__row">
        <CheeseAvatar
          v-if="isAgentHandle(message.author ?? '')"
          :size="22"
          :name="nameOf(message.author ?? '')"
          :handle="message.author"
        />
        <span v-else class="doc-thread-card__avatar" :style="{ backgroundColor: avatarColor(message.author) }">
          {{ avatarInitial(nameOf(message.author ?? '')) }}
        </span>
        <div class="doc-thread-card__body">
          <div class="doc-thread-card__meta">
            <span class="doc-thread-card__name">{{ nameOf(message.author ?? '') }}</span>
            <span class="doc-thread-card__time">{{ relTime(message.created_at) }}</span>
          </div>
          <MarkdownView
            v-if="isAgentHandle(message.author ?? '')"
            class="doc-thread-card__text md-content"
            dir="auto"
            :source="message.content"
            as="chat"
            :names="names"
          />
          <div v-else class="doc-thread-card__text doc-thread-card__text--plain" dir="auto">
            {{ plainRefs(message.content, names) }}
          </div>
        </div>
      </div>
    </div>

    <div v-if="step" class="doc-thread-card__row doc-thread-card__step" role="status">
      <CheeseAvatar :size="22" :name="agentName" state="think" />
      <span>{{ step }}</span>
      <button type="button" class="doc-thread-card__action" @click="emit('stopAgent')">
        {{ t('work.room.docAgent.stop') }}
      </button>
    </div>

    <p v-if="error" class="doc-thread-card__error" role="alert">{{ error }}</p>
    <div v-if="unknown" class="doc-thread-card__unknown" role="status">
      <span>{{ t('work.room.comments.unknown') }}</span>
      <button type="button" class="doc-thread-card__action" :disabled="busy" @click="emit('resend')">
        {{ t('work.room.comments.resend') }}
      </button>
    </div>

    <template v-if="active && writable">
      <div v-if="!resolved" class="doc-thread-card__composer">
        <textarea
          ref="input"
          v-model="text"
          rows="1"
          autocomplete="off"
          :aria-label="t('work.room.comments.reply')"
          :placeholder="t('work.room.comments.reply')"
          :title="t('work.room.comments.keyHint')"
          @keydown="onKey"
        />
      </div>
      <div class="doc-thread-card__actions">
        <button
          type="button"
          class="doc-thread-card__action"
          :disabled="busy || unknown"
          @click="resolved ? emit('reopen') : emit('resolve')"
        >
          {{ resolved ? t('work.room.comments.reopen') : t('work.room.comments.resolve') }}
        </button>
        <button
          v-if="!resolved"
          type="button"
          class="doc-thread-card__action doc-thread-card__action--primary"
          :disabled="busy || unknown || !text.trim()"
          @click="send"
        >
          {{ t('work.room.comments.reply') }}
        </button>
      </div>
    </template>
  </article>
</template>

<style scoped>
.doc-thread-card {
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: var(--surface);
  cursor: pointer;
  transition:
    background-color var(--dur-quick) var(--ease-standard),
    border-color var(--dur-quick) var(--ease-standard);
}
.doc-thread-card:hover {
  border-color: var(--line-2);
}
/* The card carries no shadow (§3.4), so the active thread is marked by the fill
   a selected row uses, not by a border the hover state already shows. */
.doc-thread-card.is-active {
  border-color: var(--line-2);
  background: var(--fill);
  cursor: default;
}
.doc-thread-card.is-resolved:not(.is-active) {
  opacity: 0.72;
}
.doc-thread-card__quote {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow: hidden;
  width: 100%;
  margin-bottom: 10px;
  padding: 0 0 0 8px;
  border: 0;
  border-left: 2px solid var(--accent);
  background: transparent;
  color: var(--muted);
  font: inherit;
  font-size: 13px;
  line-height: var(--lh-13);
  text-align: start;
  cursor: pointer;
}
.doc-thread-card__quote:hover:not(:disabled) {
  color: var(--text);
}
.doc-thread-card__quote.is-gone {
  border-left-color: var(--line-2);
  color: var(--faint);
}
.doc-thread-card__quote:disabled {
  cursor: default;
}
.doc-thread-card__message + .doc-thread-card__message,
.doc-thread-card__step {
  margin-top: 10px;
}
.doc-thread-card__row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}
.doc-thread-card__avatar {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: var(--radius-pill);
  /* stylelint-disable-next-line color-no-hex -- 压在 avatarColor() 算出来的底色上，底色不随主题变。 */
  color: #fff;
  font-size: 12px;
  font-weight: 600;
}
.doc-thread-card__body {
  flex: 1 1 auto;
  min-width: 0;
}
.doc-thread-card__meta {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.doc-thread-card__name {
  color: var(--ink);
  font-size: 13px;
  font-weight: 600;
  line-height: var(--lh-13);
  overflow-wrap: anywhere;
}
.doc-thread-card__time {
  color: var(--faint);
  font-size: 12px;
  line-height: var(--lh-12);
}
.doc-thread-card__text {
  margin-top: 2px;
  color: var(--text);
  font-size: 14px;
  line-height: var(--lh-14);
  overflow-wrap: anywhere;
}
.doc-thread-card__text--plain {
  white-space: pre-wrap;
}
.doc-thread-card__more {
  margin: 0 0 10px 30px;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--muted);
  font: inherit;
  font-size: 13px;
  line-height: var(--lh-13);
  cursor: pointer;
}
.doc-thread-card__more:hover {
  color: var(--text);
}
.doc-thread-card__step {
  align-items: center;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}
.doc-thread-card__step .doc-thread-card__action {
  margin-left: auto;
}
.doc-thread-card__step span {
  animation: docThreadBreathe 1.4s ease-in-out infinite;
}
@keyframes docThreadBreathe {
  50% {
    opacity: 0.5;
  }
}
.doc-thread-card__error {
  margin: 8px 0 0;
  color: var(--danger-ink);
  font-size: 13px;
  line-height: var(--lh-13);
}
.doc-thread-card__unknown {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 8px;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}
.doc-thread-card__composer {
  margin-top: 12px;
  padding: 8px 10px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  background: var(--surface);
}
.doc-thread-card__composer:focus-within {
  border-color: var(--muted);
}
.doc-thread-card__composer textarea {
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
.doc-thread-card__actions {
  display: flex;
  justify-content: flex-end;
  gap: 4px;
  margin-top: 8px;
}
.doc-thread-card__action {
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
.doc-thread-card__action:hover:not(:disabled) {
  background: var(--fill);
  color: var(--text);
}
.doc-thread-card__action--primary {
  color: var(--accent-ink);
}
.doc-thread-card__action:disabled {
  color: var(--faint);
  cursor: default;
}
.doc-thread-card button:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}
@media (prefers-reduced-motion: reduce) {
  .doc-thread-card__step span {
    animation: none;
  }
}
</style>
