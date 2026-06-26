<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { chatWsUrl, listBlocks } from '../api'
import type { Block, Topic, WsClientMessage, WsServerFrame } from '../types'
import CheeseAvatar from './CheeseAvatar.vue'

// Render 芝士's markdown replies to safe HTML (spec §3: AI 必须说人话, 可读).
// @mentions get a highlighted chip span — like Feishu group chat. Run after
// markdown so we only touch text nodes' rendered output.
function highlightMentions(html: string): string {
  return html.replace(
    /(^|[\s(（])@([一-龥\w-]+)/g,
    '$1<span class="mention">@$2</span>',
  )
}

function renderMarkdown(text: string): string {
  return DOMPurify.sanitize(
    highlightMentions(marked.parse(text, { async: false }) as string),
  )
}

// Plain (non-markdown) human text → escape, highlight @mentions, keep newlines.
function renderPlain(text: string): string {
  const esc = text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
  return DOMPurify.sanitize(highlightMentions(esc))
}

const props = withDefaults(
  defineProps<{
    topic: Topic | null
    // @芝士 default for new messages. Off for human-to-human (工作台), on for
    // the 1:1 private chat where 芝士 is the only counterpart.
    defaultSummon?: boolean
    // Render a self-contained composer at the bottom (used when ChatPanel is
    // dropped in standalone, e.g. the 私聊 1:1 chat in the main area). For work
    // topics WorkspaceView keeps its own spanning composer and leaves this off.
    showComposer?: boolean
    // Show the GitHub-PR-style header (话题 = PR). Only real work topics are
    // PRs — the root topic (本体) and the 1:1 private chat are NOT, so they use
    // the plain chat header instead.
    prHeader?: boolean
  }>(),
  { defaultSummon: false, showComposer: false, prHeader: false },
)

// Surface AI activity so the parent can refresh the living doc / topic list
// without a manual reload (spec §7.1 实时联动). `tool-used` fires per tool call
// (carries the short tool name); `turn-done` fires when a turn completes.
const emit = defineEmits<{
  (e: 'tool-used', name: string): void
  (e: 'turn-done'): void
  // ⤴ 升级为话题 (eval A1): the parent upgrades this message block into a topic.
  (e: 'upgrade-message', messageId: string): void
  // Open the topic an upgraded block points to (the 活引用 back-link).
  (e: 'open-topic', topicId: string): void
}>()

const AUTHOR = 'user-1'

const messages = ref<Block[]>([])
const loadingHistory = ref(false)
const connected = ref(false)
const errorMsg = ref<string | null>(null)

// In-progress assistant message being streamed via `delta` frames.
// Held separately and rendered after `messages`; replaced by the final
// `assistant_block` when the turn completes.
const streaming = ref<string | null>(null)
const awaitingReply = ref(false)

// Tool actions 芝士 performed this turn (施工现场, spec §9.1) — ephemeral.
const toolActions = ref<string[]>([])

const TOOL_LABELS: Record<string, string> = {
  create_subtopic: '拆出子话题',
  update_doc: '更新了文档',
  remember: '记入项目记忆',
  notify: '发送通知',
  request_accept: '递出验收卡',
  return_conclusion: '回流结论',
}

function toolLabel(name: string): string {
  const short = name.replace(/^mcp__cheese__/, '')
  return TOOL_LABELS[short] ?? short
}

let socket: WebSocket | null = null
const scrollRef = ref<HTMLElement | null>(null)

function scrollToBottom() {
  nextTick(() => {
    const el = scrollRef.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

function closeSocket() {
  if (socket) {
    socket.onopen = null
    socket.onmessage = null
    socket.onerror = null
    socket.onclose = null
    socket.close()
    socket = null
  }
  connected.value = false
}

function openSocket(topicId: string) {
  closeSocket()
  const ws = new WebSocket(chatWsUrl(topicId))
  socket = ws

  ws.onopen = () => {
    connected.value = true
  }
  ws.onclose = () => {
    if (socket === ws) connected.value = false
  }
  ws.onerror = () => {
    errorMsg.value = 'WebSocket 连接出错'
  }
  ws.onmessage = (ev: MessageEvent) => {
    // Guard against frames from a stale socket after topic switch.
    if (socket !== ws) return
    let frame: WsServerFrame
    try {
      frame = JSON.parse(ev.data as string) as WsServerFrame
    } catch {
      return
    }
    handleFrame(frame)
  }
}

function handleFrame(frame: WsServerFrame) {
  switch (frame.type) {
    case 'user_block':
      messages.value.push(frame.block)
      scrollToBottom()
      break
    case 'delta':
      streaming.value = (streaming.value ?? '') + frame.text
      scrollToBottom()
      break
    case 'tool': {
      toolActions.value.push(toolLabel(frame.name))
      // Emit the short tool name (e.g. update_doc, create_subtopic) so the
      // parent can refresh the relevant panel immediately.
      emit('tool-used', frame.name.replace(/^mcp__cheese__/, ''))
      scrollToBottom()
      break
    }
    case 'assistant_block':
      messages.value.push(frame.block)
      streaming.value = null
      scrollToBottom()
      break
    case 'error':
      errorMsg.value = frame.message
      streaming.value = null
      awaitingReply.value = false
      break
    case 'done':
      streaming.value = null
      awaitingReply.value = false
      emit('turn-done')
      scrollToBottom()
      break
  }
}

async function loadTopic(topic: Topic) {
  errorMsg.value = null
  streaming.value = null
  awaitingReply.value = false
  toolActions.value = []
  messages.value = []
  loadingHistory.value = true
  closeSocket()
  try {
    const payload = await listBlocks(topic.id)
    // Only apply if still the active topic (avoid race on fast switching).
    if (props.topic?.id !== topic.id) return
    messages.value = payload.data
    scrollToBottom()
    openSocket(topic.id)
  } catch (e) {
    errorMsg.value = e instanceof Error ? e.message : '加载历史失败'
  } finally {
    if (props.topic?.id === topic.id) loadingHistory.value = false
  }
}

// Send a message. `summon` (= @芝士) asks 芝士 to reply; when false the message
// is just posted (spec §7.1 默认不 @). The composer lives in WorkspaceView and
// drives this via the exposed ref, so the input bar can span chat + doc.
function send(content: string, summon: boolean): boolean {
  const trimmed = content.trim()
  if (!trimmed || !socket || socket.readyState !== WebSocket.OPEN) return false
  errorMsg.value = null
  const msg: WsClientMessage = {
    type: 'message',
    content: trimmed,
    author: AUTHOR,
    summon,
  }
  socket.send(JSON.stringify(msg))
  // Only show the "awaiting reply" affordances when 芝士 was summoned.
  if (summon) {
    awaitingReply.value = true
    streaming.value = ''
  }
  toolActions.value = []
  scrollToBottom()
  return true
}

defineExpose({ send, connected })

// The conversation stream shows messages + lightweight system lines only.
// doc/decision blocks are document state (they live in the doc panel), and AI
// tool/巡检 events belong in 现场 — neither belongs in the group chat (spec §7.1).
const visible = computed<Block[]>(() =>
  messages.value.filter(
    (m) =>
      m.kind === 'message' ||
      (m.kind === 'event' && m.author_type === 'system'),
  ),
)

// ---- Feishu group-chat helpers (Fix 2) ----
function displayName(m: Block): string {
  return m.author_type === 'ai' ? '芝士' : m.author
}
function fmtTime(iso: string): string {
  // Local HH:mm next to the name on the first of a run (not raw UTC).
  return new Date(iso).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
  })
}
// Group consecutive messages from the same author into runs: only the first of
// a run shows the avatar + name + time; the rest indent under the text column.
// An event block always breaks a run so the next message keeps its header.
function isRunStart(i: number): boolean {
  if (i === 0) return true
  const prev = visible.value[i - 1]
  const cur = visible.value[i]
  if (prev.kind === 'event' || cur.kind === 'event') return true
  return prev.author !== cur.author || prev.author_type !== cur.author_type
}

// ---- GitHub-PR-style header (Fix 4) ----
const prShortId = computed(() => props.topic?.id.slice(0, 6) ?? '')
// GitHub PR state: Open = green (semantic), Merged = muted, Draft = faint.
const prState = computed(() => {
  const s = props.topic?.status
  if (s === 'archived') return { label: 'Merged', cls: 'pr-state--merged' }
  if (s === 'draft') return { label: 'Draft', cls: 'pr-state--draft' }
  return { label: 'Open', cls: 'pr-state--open' }
})
const prBranch = computed<string>(() => {
  const t = props.topic
  if (!t) return ''
  const branch = (t as unknown as Record<string, unknown>).branch_name
  if (typeof branch === 'string' && branch) return branch
  return `topic/${t.id.slice(0, 8)}`
})

// ---- Self-contained composer (only when showComposer) ----
const draft = ref('')
const summon = ref(props.defaultSummon)

function sendDraft() {
  if (send(draft.value, summon.value)) draft.value = ''
}

function onComposerKey(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    sendDraft()
  }
}

watch(
  () => props.topic,
  (t) => {
    if (t) loadTopic(t)
    else {
      messages.value = []
      closeSocket()
    }
  },
  { immediate: true },
)

onBeforeUnmount(closeSocket)
</script>

<template>
  <div class="chat d-flex flex-column fill-height">
    <div
      v-if="!topic"
      class="flex-grow-1 d-flex align-center justify-center text-medium-emphasis"
    >
      <div class="text-center">
        <v-icon size="48" class="mb-2 text-disabled">mdi-forum-outline</v-icon>
        <div>选择或新建一个话题，开始对话</div>
      </div>
    </div>

    <template v-else>
      <!-- GitHub-PR-style header (Fix 4): only for real work topics (话题 = PR).
           The root topic (本体) and private chat use the plain header below. -->
      <div v-if="prHeader" class="pr-header px-4 py-3">
        <div class="d-flex align-center ga-2 flex-wrap">
          <span class="pr-title t-title">{{ topic.title }}</span>
          <span class="pr-num t-meta">#{{ prShortId }}</span>
          <v-spacer />
          <span
            class="status-dot"
            :class="connected ? 'status-dot--ok' : 'status-dot--muted'"
            :title="connected ? '已连接' : '未连接'"
          />
        </div>
        <div class="d-flex align-center ga-2 mt-2">
          <span class="pr-state" :class="prState.cls">
            <v-icon size="12" class="me-1">mdi-source-pull</v-icon>
            {{ prState.label }}
          </span>
          <span class="t-meta" style="color: var(--muted)">
            芝士 想把 <span class="pr-branch">{{ prBranch }}</span> 合并到
            <span class="pr-branch">main</span>
          </span>
        </div>
      </div>

      <!-- Plain chat header — normal chat (飞书私聊 / 本体): title + 已连接 -->
      <div v-else class="pr-header px-4 py-3">
        <div class="d-flex align-center ga-2">
          <span class="pr-title t-title">{{ topic.title }}</span>
          <v-spacer />
          <span
            class="status-dot"
            :class="connected ? 'status-dot--ok' : 'status-dot--muted'"
            :title="connected ? '已连接' : '未连接'"
          />
        </div>
      </div>

      <!-- Message stream — Feishu group chat: left-aligned rows, grouped runs,
           per-row hover action bar, centered system/event lines. -->
      <div ref="scrollRef" class="messages flex-grow-1 overflow-y-auto py-2">
        <div v-if="loadingHistory" class="text-medium-emphasis text-body-2 px-4 py-2">
          加载历史…
        </div>

        <template v-for="(m, i) in visible" :key="m.id">
          <!-- system / event blocks: centered, gray, small (Feishu 系统提示) -->
          <div v-if="m.kind === 'event'" class="im-event text-caption">
            <span>{{ m.content }}</span>
          </div>

          <!-- message row -->
          <div
            v-else
            class="im-row"
            :class="{ 'im-row--cont': !isRunStart(i) }"
          >
            <!-- avatar gutter: only on the first of a run -->
            <div class="im-gutter">
              <template v-if="isRunStart(i)">
                <CheeseAvatar v-if="m.author_type === 'ai'" :size="28" />
                <div v-else class="im-avatar">
                  {{ m.author.slice(0, 1).toUpperCase() }}
                </div>
              </template>
            </div>

            <div class="im-main">
              <div v-if="isRunStart(i)" class="im-meta">
                <span class="im-name">{{ displayName(m) }}</span>
                <span class="im-time">{{ fmtTime(m.created_at) }}</span>
              </div>
              <div
                v-if="m.author_type === 'ai'"
                class="im-text md-content"
                v-html="renderMarkdown(m.content)"
              />
              <div v-else class="im-text" v-html="renderPlain(m.content)" />
              <!-- 活引用 (eval A1): an upgraded block links to its new topic. -->
              <button
                v-if="m.upgraded_to_topic_id"
                type="button"
                class="im-upgraded"
                @click="emit('open-topic', m.upgraded_to_topic_id)"
              >
                <v-icon size="13">mdi-arrow-top-right</v-icon>
                已升级为话题，点击查看
              </button>
            </div>

            <!-- hover action bar, top-right of the row (Feishu). Only the action
                 we actually implement — 升级为话题 — is shown (no dead buttons). -->
            <div class="im-actions">
              <v-btn
                icon="mdi-arrow-up-bold-box-outline"
                size="x-small"
                variant="text"
                density="comfortable"
                title="升级为话题"
                @click="emit('upgrade-message', m.id)"
              />
            </div>
          </div>
        </template>

        <!-- 施工现场 — 芝士's tool calls this turn, as Feishu system lines -->
        <div
          v-for="(act, i) in toolActions"
          :key="'tool-' + i"
          class="im-event text-caption"
        >
          <span>🔧 芝士 · {{ act }}</span>
        </div>

        <!-- Live-streaming assistant message (always a run start) -->
        <div v-if="streaming !== null" class="im-row">
          <div class="im-gutter">
            <CheeseAvatar :size="28" />
          </div>
          <div class="im-main">
            <div class="im-meta">
              <span class="im-name">芝士</span>
            </div>
            <div class="im-text">
              <span class="md-content" v-html="renderMarkdown(streaming || '')" />
              <span v-if="awaitingReply" class="caret" />
            </div>
          </div>
        </div>

        <!-- End of the conversation timeline — GitHub PR's merge box. Host fills. -->
        <div class="px-4">
          <slot name="timeline-end" />
        </div>
      </div>

      <v-alert
        v-if="errorMsg"
        type="error"
        density="compact"
        class="ma-3 mt-0"
        closable
        @click:close="errorMsg = null"
      >
        {{ errorMsg }}
      </v-alert>

      <!-- Built-in composer (private chat / standalone use). -->
      <template v-if="showComposer">
        <v-divider />
        <div class="composer pa-2 px-3">
          <div class="d-flex align-center ga-2 mb-1">
            <!-- The ONE amber chip allowed: @芝士 toggle when ON. -->
            <button
              type="button"
              class="summon-chip"
              :class="{ 'summon-chip--on': summon }"
              title="@芝士 — 让芝士回复"
              @click="summon = !summon"
            >
              <v-icon v-if="summon" size="13">mdi-creation</v-icon>
              @芝士
            </button>
            <v-spacer />
          </div>
          <div class="d-flex align-end ga-2">
            <v-textarea
              v-model="draft"
              variant="plain"
              rows="1"
              auto-grow
              max-rows="6"
              hide-details
              density="comfortable"
              class="composer-input flex-grow-1"
              placeholder="发条消息…（Enter 发送，Shift+Enter 换行）"
              :disabled="!connected"
              @keydown="onComposerKey"
            />
            <v-btn
              color="primary"
              variant="flat"
              icon="mdi-send"
              size="small"
              :disabled="!connected || !draft.trim()"
              @click="sendDraft"
            />
          </div>
        </div>
      </template>
    </template>
  </div>
</template>

<style scoped>
.chat {
  background: var(--surface);
}
.messages {
  background: var(--surface);
}
.composer {
  background: var(--surface);
}
.composer-input :deep(textarea) {
  font-size: 14px;
  line-height: 1.5;
}

/* The ONE amber chip allowed: @芝士 toggle when ON. OFF = neutral. */
.summon-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 24px;
  padding: 0 9px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 500;
  background: var(--fill);
  color: var(--muted);
  cursor: pointer;
  transition: all 0.12s ease;
}
.summon-chip--on {
  background: var(--accent);
  color: #fff;
}

/* ---- GitHub PR header ---- */
.pr-header {
  background: var(--surface);
  border-bottom: 1px solid var(--line);
}
.pr-title {
  line-height: 1.3;
}
.pr-num {
  font-weight: 400;
}
/* PR state badge — semantic for Open, muted otherwise. */
.pr-state {
  display: inline-flex;
  align-items: center;
  font-size: 12px;
  font-weight: 600;
  padding: 1px 8px;
  border-radius: 6px;
}
.pr-state--open {
  color: #fff;
  background: var(--ok);
}
.pr-state--merged {
  color: var(--muted);
  background: var(--fill);
}
.pr-state--draft {
  color: var(--faint);
  background: var(--fill);
}
.pr-branch {
  font-family: var(--font-mono);
  background: var(--fill);
  padding: 0.5px 6px;
  border-radius: 4px;
  font-weight: 500;
  color: var(--muted);
}

/* ---- Feishu group chat rows (Fix 2) ---- */
.im-row {
  position: relative;
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 4px 16px;
  margin-top: 8px;
}
/* continuation rows of the same author sit tight under the first */
.im-row--cont {
  margin-top: 0;
}
.im-row:hover {
  background: var(--fill);
}
.im-gutter {
  width: 28px;
  flex: 0 0 28px;
}
/* Human avatar: --fill bg + --muted initial. No saturated per-user colors. */
.im-avatar {
  width: 28px;
  height: 28px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: var(--fill);
  color: var(--muted);
  font-size: 12px;
  font-weight: 600;
  border-radius: 8px;
}
.im-main {
  min-width: 0;
  flex: 1 1 auto;
}
.im-meta {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 2px;
}
.im-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
}
.im-time {
  font-family: var(--font-mono);
  font-size: 11.5px;
  color: var(--faint);
}
.im-text {
  font-size: 14px;
  line-height: 1.62;
  color: var(--text);
  word-break: break-word;
}
/* Live link from an upgraded block to its new topic. */
.im-upgraded {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  margin-top: 4px;
  padding: 2px 8px;
  font-size: 12px;
  color: var(--accent-ink);
  background: var(--fill);
  border: 1px solid var(--line-2);
  border-radius: 6px;
  cursor: pointer;
}
.im-upgraded:hover {
  background: var(--surface);
  border-color: var(--accent);
}
/* @mention: neutral inset, ink text — not amber. */
.im-text :deep(.mention) {
  color: var(--ink);
  background: var(--fill);
  border-radius: 4px;
  padding: 0 3px;
  font-weight: 500;
}

/* per-row hover action bar (Feishu), floats at the row's top-right */
.im-actions {
  position: absolute;
  top: -10px;
  right: 12px;
  display: flex;
  gap: 1px;
  padding: 1px;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: 8px;
  box-shadow: 0 2px 8px rgba(25, 26, 28, 0.08);
  opacity: 0;
  transition: opacity 0.12s ease;
  pointer-events: none;
}
.im-row:hover .im-actions {
  opacity: 1;
  pointer-events: auto;
}

/* system / event line: centered, faint, small */
.im-event {
  text-align: center;
  color: var(--faint);
  margin: 10px 0;
  padding: 0 16px;
  font-size: 12px;
}
.im-event span {
  background: var(--fill);
  border-radius: 10px;
  padding: 2px 10px;
  display: inline-block;
}

.caret {
  display: inline-block;
  width: 2px;
  height: 1em;
  vertical-align: text-bottom;
  margin-left: 1px;
  background: var(--ink);
  animation: blink 1s step-end infinite;
}
@keyframes blink {
  50% {
    opacity: 0;
  }
}
/* Rendered markdown for 芝士's replies (v-html → :deep). */
.md-content {
  font-size: 0.9rem;
  line-height: 1.6;
}
.md-content :deep(p) {
  margin: 0 0 8px;
}
.md-content :deep(p:last-child) {
  margin-bottom: 0;
}
.md-content :deep(h1),
.md-content :deep(h2),
.md-content :deep(h3) {
  font-size: 1.02em;
  font-weight: 600;
  margin: 10px 0 4px;
}
.md-content :deep(ul),
.md-content :deep(ol) {
  margin: 4px 0;
  padding-left: 20px;
}
.md-content :deep(li) {
  margin: 2px 0;
}
.md-content :deep(li::marker) {
  color: var(--faint);
}
.md-content :deep(a) {
  color: var(--accent-ink);
  text-decoration: none;
}
.md-content :deep(a:hover) {
  text-decoration: underline;
}
.md-content :deep(code) {
  font-family: var(--font-mono);
  background: var(--fill);
  padding: 0.5px 5px;
  border-radius: 4px;
  font-size: 0.88em;
}
.md-content :deep(pre) {
  background: var(--fill);
  padding: 11px 13px;
  border-radius: 8px;
  overflow-x: auto;
}
.md-content :deep(pre) code {
  background: none;
  padding: 0;
}
.md-content :deep(blockquote) {
  margin: 6px 0;
  padding-left: 12px;
  border-left: 2px solid var(--line-2);
  color: var(--muted);
}
</style>
