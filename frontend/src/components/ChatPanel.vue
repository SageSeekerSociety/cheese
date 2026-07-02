<script lang="ts">
// Per-topic scroll position, kept at module scope so it survives this component
// unmounting (e.g. navigating to another view) and remounting — come back to a
// topic and you land where you left off, not yanked to the bottom.
const scrollMemory = new Map<string, number>()
// How close to the bottom still counts as "at the bottom" (px).
const BOTTOM_THRESHOLD = 80
</script>

<script setup lang="ts">
import { myHandle } from '../me'
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { chatWsUrl, listBlocks } from '../api'
import {
  renderMarkdown as renderMarkdownWith,
  renderPlain as renderPlainWith,
} from '../lib/renderMessage'
import type {
  Block,
  ProjectMemberRow,
  TodoItem,
  Topic,
  WsClientMessage,
  WsServerFrame,
} from '../types'
import CheeseAvatar from './CheeseAvatar.vue'

// Message rendering (markdown / plain / reference chips) lives in
// ../lib/renderMessage so it's unit-testable; here we just bind the
// handle→name and id→title maps filled from the roster / topics props.
const mentionNames: Record<string, string> = {}
const topicTitles: Record<string, string> = {}
const refMaps = { mentionNames, topicTitles }

function renderMarkdown(text: string): string {
  return renderMarkdownWith(text, refMaps)
}

function renderPlain(text: string): string {
  return renderPlainWith(text, refMaps)
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
    // Project roster (handle→name) so <@handle> mention tokens render as chips.
    members?: ProjectMemberRow[]
    // Project topics (id→title) so <#topicId> reference tokens render as chips.
    topicList?: Topic[]
  }>(),
  {
    defaultSummon: false,
    showComposer: false,
    prHeader: false,
    members: () => [],
    topicList: () => [],
  },
)

// Surface AI activity so the parent can refresh the living doc / topic list
// without a manual reload (spec §7.1 实时联动). `tool-used` fires per tool call
// (carries the short tool name); `turn-done` fires when a turn completes.
const emit = defineEmits<{
  (e: 'tool-used', name: string): void
  // A cheese command changed a platform resource (doc/decision/topics/...) —
  // the parent refreshes that panel live, mid-turn.
  (e: 'state-changed', resource: string): void
  (e: 'turn-done'): void
  // ⤴ 升级为话题 (eval A1): the parent upgrades this message block into a topic.
  (e: 'upgrade-message', messageId: string): void
  // Open the topic an upgraded block points to (the 活引用 back-link).
  (e: 'open-topic', topicId: string): void
  // A clicked @mention chip (resolved by the parent: person → member page,
  // topic/doc → open that topic).
  (e: 'mention-click', name: string): void
  // An action card's button (decision → decisions page, milestone → calendar…).
  (e: 'open-resource', resource: string, turnId?: string): void
}>()

const AUTHOR = myHandle()

// Keep the module-level handle→name map in sync with the roster prop, so
// <@handle> tokens render with the member's display name.
watch(
  () => props.members,
  (m) => {
    for (const k of Object.keys(mentionNames)) delete mentionNames[k]
    for (const row of m) mentionNames[row.user_handle] = row.name || row.user_handle
  },
  { immediate: true, deep: true },
)
watch(
  () => props.topicList,
  (ts) => {
    for (const k of Object.keys(topicTitles)) delete topicTitles[k]
    for (const t of ts) topicTitles[t.id] = t.title
  },
  { immediate: true, deep: true },
)

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
// Live working-log todo (芝士's Task tools) for the in-progress turn (§3.1.1).
const todoItems = ref<TodoItem[]>([])
// Action cards: 芝士's cheese actions (decision/doc/...) are persisted as system
// event blocks tagged refs=["action:<resource>"] and rendered as clickable cards.
const ACTION_META: Record<string, { verb: string; btn: string }> = {
  doc: { verb: '更新了活文档', btn: '看活文档' },
  decision: { verb: '记录了一条决策', btn: '查看决策记录' },
  topics: { verb: '更新了子话题', btn: '' },
  milestone: { verb: '钉了一个里程碑', btn: '看日历' },
  accept: { verb: '递出了验收卡', btn: '去验收' },
  notify: { verb: '发了通知', btn: '' },
}

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

function todoMark(status: string): string {
  return status === 'completed' ? '✓' : status === 'in_progress' ? '◐' : '○'
}

// A system event block tagged refs=["action:<resource>"] is a clickable action
// card (decision/doc/...); returns the resource, or null for a plain event line.
function actionResource(b: Block): string | null {
  const r = (b.refs || []).find((x) => x.startsWith('action:'))
  return r ? r.slice('action:'.length) : null
}

// @mention chips are rendered via v-html; delegate clicks so the parent can
// resolve the name (person → member page, topic/doc → open it).
function onMessagesClick(e: MouseEvent) {
  const el = (e.target as HTMLElement | null)?.closest('.mention') as HTMLElement | null
  if (!el) return
  if (el.dataset.handle) emit('mention-click', el.dataset.handle)
  else if (el.dataset.topic) emit('open-topic', el.dataset.topic)
}

let socket: WebSocket | null = null
const scrollRef = ref<HTMLElement | null>(null)

// Whether the user is parked at (or near) the bottom — drives whether incoming
// messages auto-follow or leave the user's scroll position alone.
const atBottom = ref(true)

function isAtBottom(el: HTMLElement): boolean {
  return el.scrollHeight - el.scrollTop - el.clientHeight < BOTTOM_THRESHOLD
}

function scrollToBottom() {
  nextTick(() => {
    const el = scrollRef.value
    if (el) {
      el.scrollTop = el.scrollHeight
      atBottom.value = true
    }
  })
}

// Auto-follow new messages only when the user hasn't scrolled up.
function autoScroll() {
  if (atBottom.value) scrollToBottom()
}

// Save the current scroll position for the active topic (called on scroll).
function rememberScroll() {
  const el = scrollRef.value
  if (!el || !props.topic) return
  scrollMemory.set(props.topic.id, el.scrollTop)
  atBottom.value = isAtBottom(el)
}

// Restore a topic's saved scroll position, or land at the bottom if none.
function restoreScroll(topicId: string) {
  nextTick(() => {
    const el = scrollRef.value
    if (!el) return
    const saved = scrollMemory.get(topicId)
    if (saved !== undefined) {
      el.scrollTop = saved
      atBottom.value = isAtBottom(el)
    } else {
      el.scrollTop = el.scrollHeight
      atBottom.value = true
    }
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
      autoScroll()
      break
    case 'delta':
      streaming.value = (streaming.value ?? '') + frame.text
      autoScroll()
      break
    case 'tool': {
      toolActions.value.push(toolLabel(frame.name))
      // Emit the short tool name (e.g. update_doc, create_subtopic) so the
      // parent can refresh the relevant panel immediately.
      emit('tool-used', frame.name.replace(/^mcp__cheese__/, ''))
      autoScroll()
      break
    }
    case 'todo':
      // Live working-log checklist (process), updated in place.
      todoItems.value = frame.items
      autoScroll()
      break
    case 'state':
      // cheese changed a platform resource → parent refreshes that panel live.
      // The clickable record of the action is a persisted event_block (below).
      emit('state-changed', frame.resource)
      break
    case 'event_block':
      // A persisted, clickable action card (decision/doc/...) for this turn.
      messages.value.push(frame.block)
      autoScroll()
      break
    case 'assistant_block':
      messages.value.push(frame.block)
      streaming.value = null
      todoItems.value = [] // working-log done; the final message is the summary
      autoScroll()
      break
    case 'error':
      // A persisted turn failure is already in the timeline as an event block
      // (现场即事实记录); only un-persisted errors need the floating banner.
      if (!frame.persisted) errorMsg.value = frame.message
      streaming.value = null
      awaitingReply.value = false
      break
    case 'done':
      streaming.value = null
      awaitingReply.value = false
      emit('turn-done')
      autoScroll()
      break
  }
}

async function loadTopic(topic: Topic) {
  errorMsg.value = null
  streaming.value = null
  awaitingReply.value = false
  toolActions.value = []
  todoItems.value = []
  messages.value = []
  loadingHistory.value = true
  closeSocket()
  try {
    const payload = await listBlocks(topic.id)
    // Only apply if still the active topic (avoid race on fast switching).
    if (props.topic?.id !== topic.id) return
    messages.value = payload.data
    restoreScroll(topic.id)
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
// B3: reply target — the message this next send threads under (reply_to).
const replyTarget = ref<Block | null>(null)
function setReply(m: Block) {
  replyTarget.value = m
}
function clearReply() {
  replyTarget.value = null
}
function parentOf(m: Block): Block | undefined {
  return m.reply_to ? messages.value.find((x) => x.id === m.reply_to) : undefined
}
function showReplyCue(m: Block): boolean {
  // Only human replies are explicit threads. An AI message's reply_to is the
  // implicit link to the user message that triggered it — not a thread cue.
  return m.author_type === 'human' && !!parentOf(m)
}
function replySnippet(m: Block): string {
  const t = m.content.replace(/\s+/g, ' ').trim()
  return t.length > 24 ? t.slice(0, 24) + '…' : t
}
function scrollToMessage(id: string) {
  document
    .querySelector(`[data-mid="${id}"]`)
    ?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}

function send(content: string, summon: boolean): boolean {
  const trimmed = content.trim()
  if (!trimmed || !socket || socket.readyState !== WebSocket.OPEN) return false
  errorMsg.value = null
  const msg: WsClientMessage = {
    type: 'message',
    content: trimmed,
    author: AUTHOR,
    summon,
    reply_to: replyTarget.value?.id ?? undefined,
  }
  replyTarget.value = null
  socket.send(JSON.stringify(msg))
  // Only show the "awaiting reply" affordances when 芝士 was summoned. Setting
  // streaming='' immediately renders the 芝士 bubble + "正在看…" placeholder —
  // an instant ack (秒回) even before the model's first token / cold start.
  if (summon) {
    awaitingReply.value = true
    streaming.value = ''
  }
  toolActions.value = []
  todoItems.value = []
  scrollToBottom()
  return true
}

defineExpose({ send, connected })

// The conversation stream shows messages + lightweight system lines only.
// doc/decision blocks are document state (they live in the doc panel), and AI
// tool/巡检 events belong in 现场 — neither belongs in the group chat (spec §7.1).
const visible = computed<Block[]>(() => {
  const out: Block[] = []
  for (const m of messages.value) {
    if (m.kind === 'message') {
      out.push(m)
    } else if (m.kind === 'event' && m.author_type === 'system') {
      // Collapse a run of identical system lines (e.g. repeated 编辑了文档) so
      // a burst of edits shows as one line, not a wall.
      const prev = out[out.length - 1]
      if (
        prev &&
        prev.kind === 'event' &&
        prev.author_type === 'system' &&
        prev.content === m.content
      ) {
        continue
      }
      out.push(m)
    }
  }
  return out
})

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
  (t, oldT) => {
    // Save where we were in the topic we're leaving, so coming back restores it.
    if (oldT && scrollRef.value) scrollMemory.set(oldT.id, scrollRef.value.scrollTop)
    if (t) loadTopic(t)
    else {
      messages.value = []
      closeSocket()
    }
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  rememberScroll() // persist position across an unmount (e.g. leaving the view)
  closeSocket()
})
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
      <div
        ref="scrollRef"
        class="messages flex-grow-1 overflow-y-auto py-2"
        data-testid="chat-scroll"
        @scroll="rememberScroll"
        @click="onMessagesClick"
      >
        <div v-if="loadingHistory" class="text-medium-emphasis text-body-2 px-4 py-2">
          加载历史…
        </div>

        <template v-for="(m, i) in visible" :key="m.id">
          <!-- action card: 芝士's cheese action this turn, a clickable link -->
          <div v-if="m.kind === 'event' && actionResource(m)" class="action-card">
            <span class="action-verb">芝士 {{ m.content }}</span>
            <v-btn
              v-if="ACTION_META[actionResource(m)!]?.btn"
              size="x-small"
              variant="tonal"
              color="primary"
              @click="
                emit('open-resource', actionResource(m)!, m.turn_id ?? undefined)
              "
            >
              {{ ACTION_META[actionResource(m)!].btn }}
            </v-btn>
          </div>
          <!-- system / event blocks: centered, gray, small (Feishu 系统提示) -->
          <div v-else-if="m.kind === 'event'" class="im-event text-caption">
            <span>{{ m.content }}</span>
          </div>

          <!-- message row -->
          <div
            v-else
            class="im-row"
            :class="{ 'im-row--cont': !isRunStart(i) }"
            :data-mid="m.id"
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
              <!-- B3: a reply shows the message it threads under -->
              <button
                v-if="showReplyCue(m)"
                type="button"
                class="im-replied"
                @click="scrollToMessage(m.reply_to!)"
              >
                <v-icon size="12">mdi-reply</v-icon>
                回复 {{ displayName(parentOf(m)!) }}：{{ replySnippet(parentOf(m)!) }}
              </button>
              <div
                v-if="m.author_type === 'ai'"
                class="im-text md-content"
                v-html="renderMarkdown(m.content)"
              />
              <!-- 现场尊重原文: human text renders verbatim — newlines and
                   spacing preserved (pre-wrap), no markdown reflow. -->
              <div
                v-else
                class="im-text im-text--verbatim"
                v-html="renderPlain(m.content)"
              />
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
                icon="mdi-reply"
                size="x-small"
                variant="text"
                density="comfortable"
                title="回复"
                @click="setReply(m)"
              />
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

            <!-- Live working-log checklist (芝士's tasks this turn, §3.1.1) -->
            <ul v-if="todoItems.length" class="todo-list">
              <li
                v-for="t in todoItems"
                :key="t.id"
                class="todo-item"
                :class="'todo-' + t.status"
              >
                <span class="todo-mark">{{ todoMark(t.status) }}</span>
                <span class="todo-text">{{ t.subject }}</span>
              </li>
            </ul>

            <div class="im-text">
              <!-- Instant ack before the first token / during cold start -->
              <span
                v-if="awaitingReply && !streaming"
                class="text-medium-emphasis"
              >芝士 正在看…</span>
              <span v-else class="md-content" v-html="renderMarkdown(streaming || '')" />
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
        class="ma-3 mt-0 flex-shrink-0"
        closable
        @click:close="errorMsg = null"
      >
        {{ errorMsg }}
      </v-alert>

      <!-- B3: replying-to indicator — the next message threads under this one. -->
      <div v-if="replyTarget" class="reply-bar">
        <v-icon size="14" class="me-1">mdi-reply</v-icon>
        <span class="reply-bar__text">
          回复 {{ displayName(replyTarget) }}：{{ replySnippet(replyTarget) }}
        </span>
        <v-btn
          icon="mdi-close"
          size="x-small"
          variant="text"
          density="comfortable"
          @click="clearReply"
        />
      </div>

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
/* Action cards (§3.1.1 控件) — 芝士's cheese actions as clickable affordances. */
.action-card {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 2px 16px 6px;
  padding: 6px 10px;
  border: 1px solid var(--border, #e0e0e0);
  border-radius: 6px;
  background: var(--surface);
  font-size: 0.85rem;
}
.action-verb {
  color: var(--text-muted, #666);
}
/* Live working-log checklist (§3.1.1) — process, sits above the streaming text. */
.todo-list {
  list-style: none;
  margin: 2px 0 6px;
  padding: 6px 10px;
  border-left: 2px solid var(--v-theme-primary, #6750a4);
  background: rgba(103, 80, 164, 0.05);
  border-radius: 4px;
}
.todo-item {
  display: flex;
  gap: 6px;
  align-items: baseline;
  font-size: 0.85rem;
  line-height: 1.5;
}
.todo-mark {
  width: 1em;
  flex: none;
  text-align: center;
}
.todo-pending {
  color: var(--text-muted, #888);
}
.todo-in_progress {
  color: var(--v-theme-primary, #6750a4);
  font-weight: 600;
}
.todo-completed {
  color: var(--text-muted, #999);
}
.todo-completed .todo-text {
  text-decoration: line-through;
  opacity: 0.7;
}
.messages {
  background: var(--surface);
  /* A flex child's implicit min-height is its content — without this, a long
     timeline refuses to shrink and pushes whatever follows (error alert,
     reply bar) below the pane edge, clipped. */
  min-height: 0;
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
/* 现场尊重原文: exactly what the human typed, line breaks included. */
.im-text--verbatim {
  white-space: pre-wrap;
}
/* Live link from an upgraded block to its new topic. */
/* B3: the "回复 X：…" cue above a reply, and the composer reply-to bar. */
.im-replied {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  max-width: 100%;
  margin-bottom: 3px;
  padding: 1px 6px;
  font-size: 12px;
  color: var(--ink-3, #8a8f98);
  background: var(--fill);
  border-radius: 5px;
  cursor: pointer;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.im-replied:hover {
  color: var(--accent-ink);
}
.reply-bar {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 4px 12px;
  font-size: 12px;
  color: var(--ink-2, #656a72);
  background: var(--fill);
  border-top: 1px solid var(--line);
}
.reply-bar__text {
  flex: 1 1 auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
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
  color: var(--v-theme-primary, #6750a4);
  background: var(--fill);
  border-radius: 4px;
  padding: 0 3px;
  font-weight: 500;
  cursor: pointer;
}
.im-text :deep(.mention:hover) {
  text-decoration: underline;
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
