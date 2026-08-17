<script setup lang="ts">
import type { ChatAttachment, Topic } from '@/cx_types'
import type { CardPhase, TopicPhase } from '@/lib/topicState'

import { computed, nextTick, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { addComment, attachmentRawUrl } from '@/api'
import ChatPanel from '@/components/ChatPanel.vue'
import TopicAcceptCard from '@/components/TopicAcceptCard.vue'
import TopicComputePicker from '@/components/TopicComputePicker.vue'
import TopicHeader from '@/components/TopicHeader.vue'
import WorkPanel from '@/components/WorkPanel.vue'
import { usePendingAttachments } from '@/lib/attachments'
import { formatToolAction, isPlatformAction, toolLabel } from '@/lib/toolLabels'
import { topicPhase } from '@/lib/topicState'
import { myHandle } from '@/me'
import { useWorkspaceStore } from '@/stores/workspace'

// 话题视图: ONE topic header, then the chat | 工作面板 split, then the composer
// under both. Which topic is open is a route param — this component is reused
// across topic switches, so everything topic-scoped below keys off
// `props.topicId`.
defineOptions({ name: 'TopicView' })

const props = defineProps<{ projectId: string; topicId: string }>()
const router = useRouter()
const route = useRoute()
const store = useWorkspaceStore()

// 「你在看什么」进 URL (提案 A): which tab of the work panel is open, so
// 「你来看一眼这个 diff」 is a link someone can send. The panel reports its own
// moves; the address is this page's business, not the panel's.
const panelTab = computed(() => {
  const q = route.query.tab
  return typeof q === 'string' ? q : undefined
})
function onPanelTab(key: string) {
  if (panelTab.value === key) return
  // replace, not push: a tab is where you are looking, not somewhere you went.
  // Pushing would make Back walk the tabs instead of leaving the topic.
  void router.replace({ query: { ...route.query, tab: key } })
}

const AUTHOR = myHandle()

const selectedTopic = computed<Topic | null>(() => store.topics.find((t) => t.id === props.topicId) ?? null)
// The list is still on its way, so "not found" is not yet a fact.
const resolving = computed(() => !selectedTopic.value && (store.loadingTopics || store.topics.length === 0))

function openTopic(topicId: string) {
  if (topicId === props.topicId) return
  void router.push({ name: 'workspace-topic', params: { projectId: props.projectId, topicId } })
}

// ---- Layout: the chat|panel split, persisted across sessions (in the store, so
// the sidebar's own width sits in the same record). This splitter is the ONLY
// width control in the workspace now — the tool drawer used to carry a second
// one of its own (`cheesex.toolWidth`), plus a 钉住 toggle that decided whether
// the doc made room for it at all.
const focusMode = ref(false) // 专注模式 (spec §7.1): session-only, a transient mode
const panelRef = ref<{
  pulse: () => void
  highlightTurn: (turnId: string) => void
  openFile?: (path: string) => void
  refreshComments?: () => Promise<void>
} | null>(null)
const acceptRef = ref<{ reload: (silent?: boolean) => Promise<void> } | null>(null)

// Drag the chat|panel splitter: set chat's width as a % of the panes row.
function startPaneDrag(e: MouseEvent) {
  const panes = (e.currentTarget as HTMLElement).parentElement
  if (!panes) return
  const rect = panes.getBoundingClientRect()
  const move = (ev: MouseEvent) => {
    store.setChatPct(((ev.clientX - rect.left) / rect.width) * 100)
  }
  const stop = () => {
    window.removeEventListener('mousemove', move)
    window.removeEventListener('mouseup', stop)
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
  }
  window.addEventListener('mousemove', move)
  window.addEventListener('mouseup', stop)
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
}

// Bumped on every AI turn / tool use. Watched by the 文档 tab (reload the living
// doc 芝士 maintained).
const activityTick = ref(0)

// ---- Spanning composer (spec §7.1: 输入栏在对话+文档区域底部) ----
const chatRef = ref<{
  send: (content: string, summon: boolean, attachments?: ChatAttachment[]) => boolean
  connected: boolean
} | null>(null)
const draft = ref('')
const summon = ref(false) // @芝士 toggle: default OFF (人与人对话为主)
const composerReady = computed(() => !!chatRef.value?.connected)

// 图片输入: paste a screenshot / pick images → upload to the topic's worktree,
// preview above the composer, reference them on send.
const composerFileInput = ref<HTMLInputElement | null>(null)
const {
  pending: pendingAtts,
  uploading: attsUploading,
  addFiles: addAttFiles,
  onPaste: onComposerPaste,
  removeAt: removePendingAtt,
  clear: clearPendingAtts,
} = usePendingAttachments(
  () => selectedTopic.value?.id,
  (msg) => {
    store.error = msg
  }
)
function pickAttFiles() {
  composerFileInput.value?.click()
}
function onAttFilePicked(e: Event) {
  const input = e.target as HTMLInputElement
  if (input.files?.length) void addAttFiles(Array.from(input.files))
  input.value = ''
}

// 施工现场 live feed for the current topic — the 现场 tab shows it with a pulsing
// dot while the turn runs; cleared when the turn ends (the persisted transcript
// takes over as the durable record).
// platform: amber dot (cheese platform action) vs neutral dot (plain work).
const worklog = ref<{ label: string; text: string; platform: boolean }[]>([])
const working = ref(false)
const workingSince = ref<number | null>(null)

// ---- 话题此刻处在哪一段 (规则 3/4) ----
// The accept card owns its own data, but not the one word that summarises it:
// the header states where the topic stands, and the panel opens on the tab that
// stage calls for. Both live above the card, so the word travels up rather than
// the card list travelling out.
// undefined until the card box has actually answered — the header falls back to
// the topic's own status meanwhile, and the panel does not get to pick a tab on
// an answer nobody has yet.
const cardPhase = ref<CardPhase | undefined>(undefined)
const phase = computed<TopicPhase | undefined>(() => {
  if (cardPhase.value === undefined) return undefined
  return topicPhase({ status: selectedTopic.value?.status, working: working.value, card: cardPhase.value })
})
watch(
  () => props.topicId,
  () => {
    cardPhase.value = undefined
  }
)

// ---- 评论模式 (飞书 docs 风): the doc tab's selection CTA hands the anchor +
// quoted span here; the SAME composer then posts a comment instead of a chat
// message. A quote chip above the input shows the target; Esc / the ✕ exits. ----
const commentIntent = ref<{ anchorId: string | null; quote: string } | null>(null)
const commentSending = ref(false)
const composerInput = ref<{ focus?: () => void } | null>(null)

// 切换后把焦点还给输入框。不还的话 chip 自己一直握着焦点，用户接下来按的那次
// Enter 就打在 chip 上——把刚点亮的 @芝士 又静默关掉，而且那次 Enter 也不发送。
// 「先打字、再点 @芝士、再按 Enter」是很自然的顺序，走这条路的人得到的是：消息
// 正常发出、芝士不来、页面上没有任何东西说明为什么。和「忘了 @」长得一模一样。
function toggleSummon() {
  summon.value = !summon.value
  void nextTick(() => composerInput.value?.focus?.())
}

function onCommentIntent(payload: { anchorId: string | null; quote: string }) {
  commentIntent.value = payload
  void nextTick(() => composerInput.value?.focus?.())
}

async function sendDraft() {
  // Comment mode: the composer's send posts a doc comment (existing comments
  // API, same anchor/quote the drawer flow used), then returns to message mode.
  if (commentIntent.value) {
    const tid = selectedTopic.value?.id
    const text = draft.value.trim()
    if (!tid || !text || commentSending.value) return
    commentSending.value = true
    try {
      await addComment(tid, text, AUTHOR, commentIntent.value.anchorId ?? undefined, commentIntent.value.quote)
      draft.value = ''
      commentIntent.value = null
      void panelRef.value?.refreshComments?.()
    } catch (e) {
      store.reportError(e, '评论失败')
    } finally {
      commentSending.value = false
    }
    return
  }
  const ok = chatRef.value?.send(expandMentions(draft.value), summon.value, pendingAtts.value.slice())
  if (ok) {
    draft.value = ''
    clearPendingAtts()
  }
}

// @-autocomplete: the @token currently being typed at the end of the draft, and
// the matching teammates / topics it can be completed to (§3.1.1 人也能 @).
const mentionQuery = computed(() => {
  const m = draft.value.match(/@([^\s@]*)$/)
  return m ? m[1] : null
})
interface MentionItem {
  label: string
  kind: 'member' | 'topic' | 'broadcast'
  // Text written after the "@" when picked (a handle/token/name). Defaults to
  // `label` for members/topics; broadcast items insert the fixed token (all/here).
  insert: string
  // Secondary line: @handle for people, status for topics, hint for broadcast.
  sub: string
  agent: boolean
}
// 群播 (fusion-design §3): @all/@here are FIXED-LITERAL tokens (rule 4), pinned
// at the top of the menu. expandMentions turns them into <@all>/<@here>.
const BROADCAST_ITEMS: MentionItem[] = [
  {
    label: '所有人',
    kind: 'broadcast',
    insert: 'all',
    sub: '@all · 通知话题全体成员',
    agent: false,
  },
  {
    label: '在线成员',
    kind: 'broadcast',
    insert: 'here',
    sub: '@here · 通知在线成员',
    agent: false,
  },
]
const mentionMatches = computed<MentionItem[]>(() => {
  const q = mentionQuery.value
  if (q === null) return []
  const ql = q.toLowerCase()
  const broadcast = BROADCAST_ITEMS.filter((b) => b.insert.startsWith(ql) || b.label.includes(q))
  const rest: MentionItem[] = [
    ...store.members.map((m) => ({
      label: m.name || m.user_handle,
      kind: 'member' as const,
      insert: m.name || m.user_handle,
      sub: `@${m.user_handle}`,
      agent: !!m.agent,
    })),
    ...store.topics
      .filter((t) => t.kind !== 'root')
      .map((t) => ({
        label: t.title,
        kind: 'topic' as const,
        insert: t.title,
        sub: t.status === 'archived' ? '已归档' : '进行中',
        agent: false,
      })),
  ].filter((i) => i.label.toLowerCase().includes(ql))
  return [...broadcast, ...rest].slice(0, 7)
})
function pickMention(item: MentionItem) {
  draft.value = draft.value.replace(/@([^\s@]*)$/, `@${item.insert} `)
}

// IME (输入法) guard. Chrome marks the commit-Enter keydown with
// isComposing=true / keyCode 229, but Safari fires compositionend FIRST and
// the trailing keydown looks like a plain Enter (isComposing=false, keyCode
// 13). So we also track composition state ourselves and swallow any Enter
// arriving right after compositionend — that keypress belongs to the IME
// (上屏), not to "send".
let composing = false
let compositionEndedAt = -1e9
function onCompositionStart() {
  composing = true
}
function onCompositionEnd(e: CompositionEvent) {
  composing = false
  compositionEndedAt = e.timeStamp
}
function isImeKey(e: KeyboardEvent) {
  return composing || e.isComposing || e.keyCode === 229 || e.timeStamp - compositionEndedAt < 100
}

function onComposerKey(e: KeyboardEvent) {
  // Esc leaves comment mode, back to the normal message composer.
  if (e.key === 'Escape' && commentIntent.value) {
    e.preventDefault()
    commentIntent.value = null
    return
  }
  if (e.key !== 'Enter' || e.shiftKey) return
  // IME composition (拼音选字/上屏) 的回车是按给输入法的，绝不当成发送。
  if (isImeKey(e)) return
  // Only act on Enter that truly originates from the focused composer
  // textarea — guards against bubbled / fallthrough keydowns triggering an
  // unintended send when the input isn't focused.
  const t = e.target as HTMLElement | null
  if (!t || t.tagName !== 'TEXTAREA' || document.activeElement !== t) return
  e.preventDefault()
  // While the @-menu is open, Enter picks the first match instead of sending
  // (@-mentions don't apply to doc comments, so comment mode skips this).
  if (!commentIntent.value && mentionMatches.value.length) {
    pickMention(mentionMatches.value[0])
    return
  }
  void sendDraft()
}

// Human composer: turn a friendly "@名字 / @话题名" into the canonical token
// (<@handle> for a teammate, <#topicId> for a topic) at send time — longest
// patterns first so substrings don't mis-match — same encoding 芝士 uses.
function expandMentions(text: string): string {
  const subs: { pat: string; token: string }[] = [
    // 群播 tokens (fusion-design §3): @all/@here → the reserved broadcast tokens
    // the backend expands to the whole roster.
    { pat: '@all', token: '<@all>' },
    { pat: '@here', token: '<@here>' },
    // Both spellings a human naturally types: @名字 and @handle (e.g. a handle
    // pasted from someone else's message).
    ...store.members.flatMap((m) => [
      { pat: `@${m.name || m.user_handle}`, token: `<@${m.user_handle}>` },
      { pat: `@${m.user_handle}`, token: `<@${m.user_handle}>` },
    ]),
    ...store.topics.map((t) => ({ pat: `@${t.title}`, token: `<#${t.id}>` })),
  ].sort((a, b) => b.pat.length - a.pat.length)
  let out = text
  for (const s of subs) out = out.split(s.pat).join(s.token)
  return out
}

function handleTurnDone() {
  // The live feed's job is over — the persisted 现场 transcript is the record.
  working.value = false
  workingSince.value = null
  worklog.value = []
  activityTick.value += 1
  void store.refreshTopics()
  // 芝士's reply landed after our read cursor — the user is watching this
  // topic, so re-bump the cursor before refreshing badges (other topics that
  // got messages in the background DO light up).
  store.markRead(props.topicId)
  void store.refreshUnread()
}

// A `cheese <sub>` command changed a platform resource mid-turn (it runs as Bash,
// so we can't key off a tool name) — refresh the affected panel live (§3.1.1).
function handleStateChanged(resource: string) {
  if (resource === 'topics') void store.refreshTopics()
  else if (resource === 'accept') void acceptRef.value?.reload()
  else activityTick.value += 1 // doc / decision / milestone / notify → reload
}

// An action card's button → open the relevant view (§3.1.1 控件). 决策记录 has
// exactly ONE address now (`docs/decisions`, a child of this project frame) —
// it used to be a separate full page here and an in-place swap in the sidebar,
// so the same words led to two different places.
async function handleOpenResource(resource: string, turnId?: string) {
  if (resource === 'decision') {
    void router.push({ name: 'project-docs', params: { projectId: props.projectId, kind: 'decisions' } })
  } else if (resource === 'milestone') {
    void router.push({ name: 'calendar', params: { projectId: props.projectId } })
  } else if (resource === 'accept') {
    void acceptRef.value?.reload()
  } else if (resource === 'doc') {
    // B1 Phase 2: highlight the exact paragraphs this turn changed (falls back to
    // a whole-doc pulse when the turn's blocks aren't tagged). Leaving focus mode
    // re-renders the editor, which recreates its DOM — wait for that render to
    // settle before highlightTurn tags + flashes, or the flash is wiped instantly.
    focusMode.value = false
    await nextTick()
    if (turnId) panelRef.value?.highlightTurn(turnId)
    else panelRef.value?.pulse()
  }
  // topics: the topic panel is already in view next to the chat.
}

// A clicked <@handle> mention chip → open that teammate's member page.
function handleMentionClick(handle: string) {
  void router.push({ name: 'member', params: { projectId: props.projectId, handle } })
}

function handleToolUsed(name: string, input?: Record<string, unknown>) {
  if (!working.value) workingSince.value = Date.now()
  working.value = true
  worklog.value.push({
    label: toolLabel(name),
    text: formatToolAction(name, input),
    platform: isPlatformAction(name, input),
  })
  if (name === 'update_doc') {
    activityTick.value += 1
  } else if (name === 'create_subtopic') {
    void store.refreshTopics()
  } else if (name === 'request_accept') {
    // 芝士 递出验收卡: refresh the banner so it shows up immediately.
    void acceptRef.value?.reload()
  }
}

// ⤴ 升级为话题 from a message bubble (eval A1).
async function handleUpgradeMessage(messageId: string) {
  const upgraded = await store.upgradeMessage(messageId)
  if (upgraded) openTopic(upgraded.id)
}

// Everything topic-scoped resets when the URL names a different topic.
watch(
  () => props.topicId,
  (id) => {
    worklog.value = []
    working.value = false
    workingSince.value = null
    clearPendingAtts() // pending images belong to the topic they were typed in
    commentIntent.value = null // a comment target belongs to its topic's doc
    if (id) store.markRead(id)
  },
  { immediate: true }
)
</script>

<template>
  <div class="topic-view d-flex flex-column fill-height" style="min-width: 0">
    <div v-if="!selectedTopic" class="flex-grow-1 d-flex align-center justify-center">
      <v-progress-circular v-if="resolving" indeterminate color="primary" />
      <div v-else class="text-center">
        <div class="t-body c-muted">这个话题不存在</div>
        <div class="t-meta mt-1">它可能已被删除，或不属于这个项目</div>
      </div>
    </div>

    <template v-else>
      <!-- 一条话题头部，横跨对话和工作面板 -->
      <TopicHeader
        :topic="selectedTopic"
        :phase="phase"
        :members="store.members"
        :me="AUTHOR"
        :connected="composerReady"
        :focus="focusMode"
        @toggle-focus="focusMode = !focusMode"
      />

      <div class="panes d-flex flex-grow-1" style="min-width: 0; min-height: 0; position: relative">
        <ChatPanel
          v-show="!focusMode"
          ref="chatRef"
          class="col col-chat"
          :style="{ flex: `0 0 ${store.chatPct}%` }"
          :topic="selectedTopic"
          hide-header
          :members="store.members"
          :topic-list="store.topics"
          @turn-done="handleTurnDone"
          @tool-used="handleToolUsed"
          @state-changed="handleStateChanged"
          @mention-click="handleMentionClick"
          @open-file="(p: string) => panelRef?.openFile?.(p)"
          @open-resource="handleOpenResource"
          @upgrade-message="handleUpgradeMessage"
          @open-topic="openTopic"
        >
          <!-- 成果待采纳框，放在对话时间线末尾 (GitHub PR 的合并框样式) -->
          <template #timeline-end>
            <TopicAcceptCard
              ref="acceptRef"
              :topic-id="selectedTopic.id"
              :topic-status="selectedTopic.status"
              @phase="cardPhase = $event"
              @review="onPanelTab('changes')"
            />
          </template>
        </ChatPanel>
        <div
          v-if="!focusMode"
          class="pane-resizer"
          title="拖动调整宽度，双击复位"
          @mousedown.prevent="startPaneDrag"
          @dblclick="store.setChatPct(50)"
        />
        <WorkPanel
          ref="panelRef"
          class="col col-doc"
          :style="{ flex: '1 1 0', minWidth: 0 }"
          :topic="selectedTopic"
          :activity-tick="activityTick"
          :worklog="worklog"
          :working="working"
          :working-since="workingSince"
          :topic-list="store.topics"
          :tab="panelTab"
          :phase="phase"
          @open-topic="openTopic"
          @mention-click="handleMentionClick"
          @comment-intent="onCommentIntent"
          @update:tab="onPanelTab"
        />
      </div>

      <!-- 输入栏 spans 对话 + 工作面板, not the rail (spec §7.1) -->
      <v-divider />
      <div class="composer pa-2 px-3">
        <div class="d-flex align-center ga-2 mb-1">
          <span class="chip-neutral"> <v-icon size="12">mdi-pound</v-icon>本话题 </span>
          <span
            v-if="selectedTopic.status === 'archived'"
            class="d-inline-flex align-center ga-1 c-faint"
            style="font-size: 12px"
          >
            <span class="status-dot status-dot--muted" />已归档
          </span>
          <!-- The ONE amber chip allowed: @芝士 toggle when ON. -->
          <button
            type="button"
            class="summon-chip"
            :class="{ 'summon-chip--on': summon }"
            title="让芝士回复"
            @click="toggleSummon"
          >
            <v-icon v-if="summon" size="13">mdi-creation</v-icon>
            @芝士
          </button>
          <v-spacer />
          <!-- 会话级算力 (v4): pick where this topic's turns run; locks on the
               first message. Keyed by topic so it reloads on switch. -->
          <TopicComputePicker :key="selectedTopic.id" :topic-id="selectedTopic.id" />
        </div>
        <!-- 评论模式: quote chip above the input — what this send will
             comment on. The ✕ button / Esc exits back to normal message mode. -->
        <div v-if="commentIntent" class="comment-mode-chip">
          <v-icon size="14" class="comment-mode-chip__icon"> mdi-comment-quote-outline </v-icon>
          <span class="comment-mode-chip__label">评论</span>
          <span class="comment-mode-chip__quote">{{ commentIntent.quote }}</span>
          <button type="button" class="comment-mode-chip__x" title="退出评论模式" @click="commentIntent = null">
            <v-icon size="13">mdi-close</v-icon>
          </button>
        </div>
        <!-- @-autocomplete: pick a teammate / topic while typing @ -->
        <div v-if="mentionMatches.length && !commentIntent" class="mention-menu">
          <button
            v-for="(mm, i) in mentionMatches"
            :key="mm.kind + mm.insert"
            type="button"
            class="mention-menu-item"
            @click="pickMention(mm)"
          >
            <span v-if="mm.kind === 'broadcast'" class="mention-avatar mention-avatar--broadcast">
              <v-icon size="13">mdi-bullhorn-outline</v-icon>
            </span>
            <span
              v-else-if="mm.kind === 'member'"
              class="mention-avatar"
              :class="{ 'mention-avatar--agent': mm.agent }"
            >
              {{ mm.label.slice(0, 1).toUpperCase() }}
            </span>
            <span v-else class="mention-avatar mention-avatar--topic">
              <v-icon size="13">mdi-pound</v-icon>
            </span>
            <span class="mention-menu-name">{{ mm.label }}</span>
            <span v-if="mm.agent" class="mention-agent-badge">AI</span>
            <span class="mention-menu-sub">{{ mm.sub }}</span>
            <span v-if="i === 0" class="mention-menu-hint">Enter</span>
          </button>
        </div>
        <!-- 图片输入: images waiting to go with the next send. -->
        <div v-if="pendingAtts.length || attsUploading" class="att-strip">
          <div v-for="(a, i) in pendingAtts" :key="a.path" class="att-thumb">
            <img :src="attachmentRawUrl(selectedTopic.id, a.path)" :alt="a.path" />
            <button type="button" class="att-remove" title="移除" @click="removePendingAtt(i)">
              <v-icon size="12">mdi-close</v-icon>
            </button>
          </div>
          <v-progress-circular v-if="attsUploading" indeterminate size="18" width="2" />
        </div>
        <div class="d-flex align-end ga-2">
          <v-textarea
            ref="composerInput"
            v-model="draft"
            variant="plain"
            rows="1"
            auto-grow
            max-rows="6"
            hide-details
            density="comfortable"
            class="composer-input flex-grow-1"
            :placeholder="commentIntent ? '输入评论…' : summon ? '告诉芝士要做什么…' : '输入消息…'"
            :disabled="!composerReady && !commentIntent"
            @keydown="onComposerKey"
            @paste="onComposerPaste"
            @compositionstart="onCompositionStart"
            @compositionend="onCompositionEnd"
          />
          <input
            ref="composerFileInput"
            type="file"
            accept="image/png,image/jpeg,image/gif,image/webp"
            multiple
            class="d-none"
            @change="onAttFilePicked"
          />
          <v-btn
            icon="mdi-image-plus-outline"
            variant="text"
            size="small"
            title="发送图片"
            :disabled="!composerReady"
            @click="pickAttFiles"
          />
          <v-btn
            color="primary"
            variant="flat"
            icon="mdi-send"
            size="small"
            :loading="commentSending"
            :disabled="commentIntent ? !draft.trim() : !composerReady || (!draft.trim() && !pendingAtts.length)"
            @click="sendDraft"
          />
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.topic-view {
  flex: 1 1 auto;
  overflow: hidden;
  /* 底色分层：侧栏坐在 background (--canvas) 上，内容区是它上面那张 surface。
     这条视图以前不画底、直接透出 body 的 --canvas，于是侧栏和正文同色，两者
     之间只剩一条边线在撑。 */
  background: var(--surface);
}
.col {
  min-width: 0;
}
/* Draggable splitter between chat and panel (replaces the static divider). */
.pane-resizer {
  flex: 0 0 5px;
  cursor: col-resize;
  background: var(--line);
  transition: background 0.12s ease;
}
.pane-resizer:hover {
  background: var(--accent);
}
.composer {
  background: var(--surface);
}
/* 图片输入: pending images above the composer, each with a remove button. */
.att-strip {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 6px 2px;
}
.att-thumb {
  position: relative;
  line-height: 0;
}
.att-thumb img {
  width: 56px;
  height: 56px;
  object-fit: cover;
  border-radius: 8px;
  border: 1px solid var(--line);
  background: var(--fill);
}
.att-remove {
  display: inline-flex;
  position: absolute;
  top: -6px;
  right: -6px;
  width: 18px;
  height: 18px;
  align-items: center;
  justify-content: center;
  line-height: 1;
  cursor: pointer;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 50%;
  color: var(--muted);
}
.att-remove:hover {
  color: var(--ink);
}
/* 评论模式 quote chip: the doc span this composer send will comment on. */
.comment-mode-chip {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
  padding: 5px 10px;
  /* 强调靠 wash 底色 + 行内的琥珀图标，不靠左竖条：左条纹在这套设计语言里只
     留给引用块和树的结构线。四角同圆之后它读起来才是一颗 chip。 */
  border-radius: var(--radius-md);
  background: rgba(var(--v-theme-primary), 0.07);
}
.comment-mode-chip__icon {
  color: rgb(var(--v-theme-primary));
  flex: 0 0 auto;
}
.comment-mode-chip__label {
  font-size: 12px;
  font-weight: 600;
  color: rgb(var(--v-theme-primary));
  flex: 0 0 auto;
}
.comment-mode-chip__quote {
  flex: 1 1 auto;
  min-width: 0;
  font-size: 12px;
  color: var(--muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.comment-mode-chip__x {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  padding: 2px 4px;
  line-height: 1;
  cursor: pointer;
  background: transparent;
  border: none;
  border-radius: var(--radius-sm);
  color: var(--faint);
}
.comment-mode-chip__x:hover {
  color: var(--muted);
  background: rgba(var(--v-theme-primary), 0.1);
}

/* @-autocomplete dropdown (§3.1.1) */
.mention-menu {
  display: flex;
  flex-direction: column;
  margin-bottom: 6px;
  border: 1px solid var(--line);
  border-radius: 8px;
  overflow: hidden;
  background: var(--surface);
  box-shadow: var(--shadow-2);
}
.mention-menu-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 12px;
  min-height: 36px;
  text-align: left;
  font-size: 0.85rem;
  cursor: pointer;
}
.mention-menu-item:hover {
  background: var(--fill);
}
.mention-avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  font-size: 0.7rem;
  font-weight: 700;
  /* 没有真实头像时的中性圆点。原来是钉死的 #8a94a3 + #fff，理由写的是「和
     avatarColor() 算出来的头像同一类，两个主题下同值」——但这一个并不是
     avatarColor() 算出来的，它就是一个字面灰，那条豁免对它不成立。换成 token
     还顺手把对比度从 2.9:1 提到 5.0:1。 */
  color: var(--surface);
  background: var(--muted);
  flex: none;
}
.mention-avatar--agent {
  background: var(--accent);
}
.mention-avatar--broadcast {
  background: var(--ink);
}
.mention-avatar--topic {
  background: var(--fill);
  color: var(--muted);
}
.mention-menu-name {
  font-weight: 500;
}
.mention-agent-badge {
  font-size: 0.65rem;
  font-weight: 600;
  padding: 0 5px;
  border-radius: var(--radius-sm);
  color: rgb(var(--v-theme-primary));
  background: rgba(var(--v-theme-primary), 0.12);
}
.mention-menu-sub {
  font-size: 0.75rem;
  color: var(--faint);
}
.mention-menu-hint {
  margin-left: auto;
  font-size: 0.7rem;
  color: var(--faint);
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
  /* on-primary，和 ChatPanel 的同名 chip 一致 —— 琥珀填充上的字全站只有这一种做法。
     浅色下 Vuetify 对 #F57F17 推出来的就是 #fff，所以这里仍然是白字，2.65:1，低于
     AA 的 4.5:1。这是已知豁免，不是漏掉的 bug：2026-08-16 项目负责人拍板「保持白字」
     —— 改成深墨确实能到 5.84:1，但主操作上的字会从白变深、观感肉眼可见地变，而品牌
     琥珀 #F57F17 本身已锁定不动；同一次拍板里，琥珀选中指示条的 2.49:1 也按同样理由
     接受了。深色侧不受影响：on-primary 对 #FFA733 推成 #000，10.8:1，过 AA。
     所以：别把它「修好」成钉死的深墨 —— 那是被推翻过的方案。 */
  color: rgb(var(--v-theme-on-primary));
}

@media (max-width: 960px) {
  .panes {
    flex-direction: column;
  }
}
</style>
