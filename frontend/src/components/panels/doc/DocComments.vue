<script setup lang="ts">
// 文档底部的常驻评论区（飞书 docs 风）：所有评论都在这儿，锚在某一段的带一颗引用
// chip，点它回到那一段；页级评论平铺。
//
// 它从 PanelDoc 里搬出来，是因为规则 5 之后它自足了：写评论的输入框长在这里，发和
// 收都在这里，外面只需要知道「有人点了某一段」和「刚发了一条」。评论的**拉取**仍
// 归宿主 —— 同一次请求还要喂编辑器里的下划线装饰，拆开会变成两次请求两份真相。
import type { Block } from '../../../cx_types'

import { nextTick, ref } from 'vue'

import { addComment } from '../../../api'
import { relTime } from '../../../lib/relTime'
import { myHandle } from '../../../me'

const props = defineProps<{
  topicId: string | null
  comments: Block[]
  /** The doc's paragraphs, so an anchored comment can name the one it points at. */
  anchorNodes: Block[]
}>()

const emit = defineEmits<{
  /** A quote chip was clicked: scroll to and flash that paragraph. */
  (e: 'locate-node', nodeId: string): void
  /** A comment was posted — the host re-fetches (it also feeds the underlines). */
  (e: 'posted'): void
}>()

const AUTHOR = myHandle()

// 页级评论折叠态 (Feishu-style, collapsed head keeps the doc quiet).
const folded = ref(false)

// A short label for a doc node, used as the anchor-chip fallback when a comment
// has no quoted span. The node's own content is either AI- or human-authored
// text; we only ever truncate it for display (never to derive semantics).
function nodeLabel(content: string): string {
  const t = content.replace(/^#+\s*/, '').trim()
  return t.length > 22 ? t.slice(0, 22) + '…' : t || '(空段落)'
}
/** The paragraph a comment points at (or null for a whole-doc comment). */
function commentAnchor(c: Block): Block | null {
  return c.reply_to ? props.anchorNodes.find((n) => n.id === c.reply_to) ?? null : null
}

// ---- 写评论 ----
// 批注归批注，聊天归聊天: this input used to be the workspace's shared chat box,
// silently retargeted by selecting text. It lives where the comments are now.
const draft = ref<{ anchorId: string | null; quote: string } | null>(null)
const text = ref('')
const sending = ref(false)
const errorMsg = ref<string | null>(null)
const input = ref<{ focus?: () => void } | null>(null)

function open(target: { anchorId: string | null; quote: string }) {
  folded.value = false
  draft.value = target
  text.value = ''
  errorMsg.value = null
  void nextTick(() => input.value?.focus?.())
}

function cancel() {
  draft.value = null
  text.value = ''
}

async function submit() {
  const tid = props.topicId
  const target = draft.value
  const body = text.value.trim()
  if (!tid || !target || !body || sending.value) return
  sending.value = true
  errorMsg.value = null
  try {
    await addComment(tid, body, AUTHOR, target.anchorId ?? undefined, target.quote)
    cancel()
    emit('posted')
  } catch (e) {
    errorMsg.value = e instanceof Error ? e.message : '评论失败'
  } finally {
    sending.value = false
  }
}

function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    e.preventDefault()
    cancel()
    return
  }
  if (e.key !== 'Enter' || e.shiftKey || e.isComposing) return
  e.preventDefault()
  void submit()
}

// 下划线点回来的那一跳：卡片在这个组件的 DOM 里，所以展开和高亮也归这里。
function locate(commentId: string) {
  folded.value = false
  void nextTick(() => {
    const card = document.querySelector(`[data-comment-card="${commentId}"]`)
    card?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    card?.classList.add('comment-card--pulse')
    window.setTimeout(() => card?.classList.remove('comment-card--pulse'), 1600)
  })
}

defineExpose({ open, locate })
</script>

<template>
  <div class="doc-comments">
    <!-- Collapsible head; ONE 写评论 action, and it opens the input
       that lives right here — 批注归批注，聊天归聊天. -->
    <div class="doc-comments__head">
      <button
        type="button"
        class="doc-comments__fold"
        :title="folded ? '展开评论' : '收起评论'"
        @click="folded = !folded"
      >
        <v-icon size="15" class="c-faint">
          {{ folded ? 'mdi-chevron-right' : 'mdi-chevron-down' }}
        </v-icon>
        <v-icon size="15" class="c-faint">mdi-comment-text-outline</v-icon>
        评论
        <span v-if="comments.length" class="doc-comments__count">
          {{ comments.length }}
        </span>
      </button>
      <v-spacer />
      <v-btn
        icon="mdi-plus"
        size="x-small"
        variant="tonal"
        color="primary"
        title="写评论"
        @click="open({ anchorId: null, quote: '' })"
      />
    </div>
    <template v-if="!folded">
      <!-- 写评论: anchored to a paragraph when it came from a
         selection, page-level when it came from the ＋. -->
      <div v-if="draft" class="comment-draft">
        <div v-if="draft.quote" class="comment-draft__quote">
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
          placeholder="输入评论…"
          title="Enter 发送，Shift+Enter 换行"
          @keydown="onKey"
        />
        <div v-if="errorMsg" class="comment-draft__error">{{ errorMsg }}</div>
        <div class="d-flex align-center ga-2 justify-end">
          <v-btn size="small" variant="text" :disabled="sending" @click="cancel"> 取消 </v-btn>
          <v-btn
            size="small"
            color="primary"
            variant="flat"
            :loading="sending"
            :disabled="!text.trim()"
            @click="submit"
          >
            评论
          </v-btn>
        </div>
      </div>
      <div v-for="c in comments" :key="c.id" class="doc-comments__item" :data-comment-card="c.id">
        <span class="doc-comments__avatar">
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
            title="定位到该段"
            @click="emit('locate-node', c.reply_to!)"
          >
            {{ c.anchor_quote || nodeLabel(commentAnchor(c)!.content) }}
          </button>
          <div v-else-if="c.reply_to || c.anchor_quote" class="doc-comments__stale">原段落已改动</div>
          <div class="doc-comments__text">{{ c.content }}</div>
        </div>
      </div>
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
  font-size: 0.72rem;
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
  font-size: 0.66rem;
}

/* 飞书 docs 风常驻评论区 at the bottom of the document column. */
.doc-comments {
  max-width: 720px;
  margin: 40px auto 0;
  padding-top: 14px;
  border-top: 1px solid var(--line-2);
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
  font-size: 11px;
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
  border: 1px solid var(--line-2);
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
  /* 搬迁保留原样。这一对字面色其实站不住脚 —— 它引用的是 avatarColor() 那条
     豁免（算出来的定色底 + 定色墨），但这个 #8a94a3 不是算出来的，就是一个字面
     灰。同一处缺陷在 TopicView 的 .mention-avatar 上已经按 var(--surface) /
     var(--muted) 修过（顺带把对比度从 2.9:1 提到 5.0:1）。这一轮是纯搬迁，不夹带
     修改；这两行留给「现场 + 预览打磨」那张卡。 */
  /* stylelint-disable-next-line color-no-hex -- 见上，搬迁保留，已记入报告 */
  color: #fff;
  /* stylelint-disable-next-line color-no-hex -- 见上，搬迁保留，已记入报告 */
  background: #8a94a3;
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
  font-size: 12.5px;
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
/* Anchored comment's quote chip: the message-quote visual language (amber left
   bar over a faint amber ground). Click → scroll + flash the paragraph. */
.doc-comments__chip {
  display: block;
  max-width: 100%;
  text-align: left;
  border: none;
  border-left: 2px solid var(--accent);
  background: rgba(var(--v-theme-primary), 0.06);
  border-top-right-radius: var(--radius-sm);
  border-bottom-right-radius: var(--radius-sm);
  padding: 3px 8px;
  margin: 2px 0 4px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--muted);
  cursor: pointer;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  transition: background 0.15s;
}
.doc-comments__chip:hover {
  background: rgba(var(--v-theme-primary), 0.13);
}
/* The anchor node no longer exists — the paragraph was edited away. */
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
  font-size: 13.5px;
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
