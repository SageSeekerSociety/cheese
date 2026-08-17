<script setup lang="ts">
import type { AcceptCard, ChatAttachment, PrChecks, Topic } from '@/cx_types'

import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import {
  acceptCard,
  addComment,
  approveCard,
  attachmentRawUrl,
  getAcceptCards,
  getPrChecks,
  mergeCardAnyway,
  reassignCard,
  rejectCard,
  revokeCard,
} from '@/api'
import ChatPanel from '@/components/ChatPanel.vue'
import DocPanel from '@/components/DocPanel.vue'
import TopicComputePicker from '@/components/TopicComputePicker.vue'
import TopicMembers from '@/components/TopicMembers.vue'
import { usePendingAttachments } from '@/lib/attachments'
import { deliveryNoteTone, deliveryStageOf } from '@/lib/deliveryStage'
import { formatToolAction, isPlatformAction, toolLabel } from '@/lib/toolLabels'
import { myHandle } from '@/me'
import { useWorkspaceStore } from '@/stores/workspace'

// 话题视图: the chat | doc panes and the composer under them. Which topic is
// open is a route param — this component is reused across topic switches, so
// everything topic-scoped below keys off `props.topicId`.
defineOptions({ name: 'TopicView' })

const props = defineProps<{ projectId: string; topicId: string }>()
const router = useRouter()
const store = useWorkspaceStore()

const AUTHOR = myHandle()

const selectedTopic = computed<Topic | null>(() => store.topics.find((t) => t.id === props.topicId) ?? null)
// The list is still on its way, so "not found" is not yet a fact.
const resolving = computed(() => !selectedTopic.value && (store.loadingTopics || store.topics.length === 0))

function openTopic(topicId: string) {
  if (topicId === props.topicId) return
  void router.push({ name: 'workspace-topic', params: { projectId: props.projectId, topicId } })
}

// ---- Layout: the chat|doc split, persisted across sessions (in the store, so
// the sidebar's own width sits in the same record).
const focusMode = ref(false) // 专注模式 (spec §7.1): session-only, a transient mode
const docRef = ref<{
  pulse: () => void
  highlightTurn: (turnId: string) => void
  openFile?: (path: string) => void
  refreshComments?: () => Promise<void>
} | null>(null)

// Drag the chat|doc splitter: set chat's width as a % of the panes row.
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

// Bumped on every AI turn / tool use. Watched by DocPanel (reload the living
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

// 施工现场 live feed for the current topic — DocPanel's 现场 drawer shows it
// with a pulsing dot while the turn runs; cleared when the turn ends (the
// persisted transcript takes over as the durable record).
// platform: amber dot (cheese platform action) vs neutral dot (plain work).
const worklog = ref<{ label: string; text: string; platform: boolean }[]>([])
const working = ref(false)
const workingSince = ref<number | null>(null)

// ---- 评论模式 (飞书 docs 风): the doc panel's selection CTA hands the anchor +
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
      void docRef.value?.refreshComments?.()
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

// ---- 采纳卡 (eval C5/A3): a banner at the end of the timeline ----
const acceptCards = ref<AcceptCard[]>([])
const acceptBusy = ref(false)
const rejectNote = ref('')
const showRejectInput = ref(false)
// 人工放行 (App 采纳等 CI 再合): 明知检查没全绿仍合并。默认拒绝、显式放行，所以
// 它藏在一个要先展开、再填理由的小表单后面——不是一个可以顺手点到的按钮。
const showForceMergeInput = ref(false)
const forceMergeReason = ref('')

// Newest pending card (the list comes newest-first). A card in `conflict`
// (采纳时合并冲突，芝士被派去解决) keeps the merge box up — as a STATE, with a
// retry button — instead of pretending the accept went through.
const pendingCard = computed<AcceptCard | null>(
  () => acceptCards.value.find((c) => c.status === 'pending' || c.status === 'conflict') ?? null
)
// The accepted card on an archived topic — its presence lets us offer 撤回采纳.
const acceptedCard = computed<AcceptCard | null>(() => acceptCards.value.find((c) => c.status === 'accepted') ?? null)

// 交付进度 (两阶段采纳, 2026-08-09): the human already clicked 采纳 and the PR
// is open — CI and the merge run for hours after that. Without this branch
// the whole merge box vanishes the moment someone accepts, and nothing on
// screen says the delivery is still in flight. Read-only: the decision was
// already made, nobody should be asked to click a second time.
const deliveringCard = computed<AcceptCard | null>(() => acceptCards.value.find((c) => c.status === 'pr_open') ?? null)
// 步骤由后端下发（`card.stages`）——哪些步骤存在取决于项目的 forge，浏览器看不见。
// 这里只把它们配上文案，见 lib/deliveryStage.ts。
const deliveryStage = computed(() => (deliveringCard.value ? deliveryStageOf(deliveringCard.value) : null))
// 交付途中后端把阶段信息/故障写在卡的 note 上（CI 红了、GitHub 拒绝
// 合并、轮询用的 token 失效），那是这些事唯一露头的地方，照原样显示。
const deliveryNote = computed(() => {
  const note = deliveringCard.value?.note ?? ''
  const tone = deliveryNoteTone(note)
  return tone ? { text: note, tone } : null
})

// 机器闸门 (eval C2): the newest card while the platform check runs / after it
// failed. Only the newest card can be in a gate state (one in-flight card per
// topic is enforced server-side).
const gateCard = computed<AcceptCard | null>(() => {
  const c = acceptCards.value[0]
  return c && (c.status === 'pending_gate' || c.status === 'gate_failed' || c.status === 'gate_blocked') ? c : null
})
const showGateOutput = ref(false)

// While the check runs (it can take minutes), poll the card until it settles.
let gatePollTimer: number | null = null
watch(
  () => gateCard.value?.status === 'pending_gate',
  (running) => {
    if (running && gatePollTimer === null) {
      gatePollTimer = window.setInterval(() => loadAcceptCard(true), 2500)
    } else if (!running && gatePollTimer !== null) {
      window.clearInterval(gatePollTimer)
      gatePollTimer = null
    }
  }
)
onUnmounted(() => {
  if (gatePollTimer !== null) window.clearInterval(gatePollTimer)
})

// 采纳 PR 化 (#188 §5.1): live CI state of the card's PR. Polled slowly while
// such a card is on screen — checks take minutes, not seconds. Both the
// pending card (人还没点) and the delivering one (点完了，CI 在跑) ride the
// same PR, and /pr-checks answers for any card that has a pr_number.
const prCheckCard = computed<AcceptCard | null>(() => pendingCard.value ?? deliveringCard.value)
const prChecks = ref<PrChecks | null>(null)
let prPollTimer: number | null = null
async function loadPrChecks() {
  const tid = props.topicId
  if (!prCheckCard.value?.pr_number) return
  try {
    const payload = await getPrChecks(tid)
    if (props.topicId === tid) prChecks.value = payload
  } catch {
    // Best-effort; the PR row just shows the link without CI state.
  }
}
watch(
  () => (prCheckCard.value?.pr_number ? props.topicId : null),
  (active) => {
    prChecks.value = null
    if (active && prPollTimer === null) {
      void loadPrChecks()
      prPollTimer = window.setInterval(() => {
        void loadPrChecks()
        // 交付中卡本身也在变（合并时间、note、最终 accepted），跟着一起刷新，
        // 否则界面会停在采纳那一刻的快照上直到用户手动切话题。
        if (deliveringCard.value) void loadAcceptCard(true)
      }, 15000)
    } else if (!active && prPollTimer !== null) {
      window.clearInterval(prPollTimer)
      prPollTimer = null
    }
  },
  { immediate: true }
)
onUnmounted(() => {
  if (prPollTimer !== null) window.clearInterval(prPollTimer)
})

// 主分支保护 (spec §4.4): my vote toward the pending card's accept.
async function onApproveCard() {
  const card = pendingCard.value
  if (!card) return
  acceptBusy.value = true
  try {
    await approveCard(card.id, AUTHOR)
    await loadAcceptCard(true)
  } catch (e) {
    store.reportError(e, '批准失败')
  } finally {
    acceptBusy.value = false
  }
}

async function onReassignCard(handle: string) {
  const card = pendingCard.value
  if (!card || handle === card.reviewer_handle) return
  acceptBusy.value = true
  try {
    await reassignCard(card.id, handle)
    await loadAcceptCard()
  } catch (e) {
    store.reportError(e, '改验收人失败')
  } finally {
    acceptBusy.value = false
  }
}

async function loadAcceptCard(silent = false) {
  // silent = a background refresh (gate polling / after a vote): keep the
  // current cards on screen instead of blanking the box for a beat.
  if (!silent) {
    acceptCards.value = []
    showRejectInput.value = false
    rejectNote.value = ''
    showGateOutput.value = false
  }
  const tid = props.topicId
  if (!tid) return
  try {
    const payload = await getAcceptCards(tid)
    if (props.topicId === tid) acceptCards.value = payload.data
  } catch {
    // Best-effort; the banner just stays hidden.
  }
}

async function onAcceptCard() {
  const card = pendingCard.value
  if (!card) return
  acceptBusy.value = true
  try {
    const updated = await acceptCard(card.id, AUTHOR)
    if (updated.status === 'conflict') {
      store.error = '合并冲突，这次没有归档——芝士已被派去解决，它汇报后再点「重试采纳」。'
    }
    await Promise.all([loadAcceptCard(), store.refreshTopicRow(props.topicId)])
  } catch (e) {
    store.reportError(e, '采纳失败')
  } finally {
    acceptBusy.value = false
  }
}

async function onRevokeCard() {
  const card = acceptedCard.value
  if (!card) return
  acceptBusy.value = true
  try {
    await revokeCard(card.id, AUTHOR)
    await Promise.all([loadAcceptCard(), store.refreshTopicRow(props.topicId)])
  } catch (e) {
    store.reportError(e, '撤回采纳失败')
  } finally {
    acceptBusy.value = false
  }
}

async function onForceMerge() {
  const card = deliveringCard.value
  if (!card) return
  acceptBusy.value = true
  try {
    await mergeCardAnyway(card.id, forceMergeReason.value)
    showForceMergeInput.value = false
    forceMergeReason.value = ''
    await Promise.all([loadAcceptCard(), store.refreshTopicRow(props.topicId)])
  } catch (e) {
    store.reportError(e, '人工放行失败')
  } finally {
    acceptBusy.value = false
  }
}

async function onRejectCard() {
  const card = pendingCard.value
  if (!card) return
  acceptBusy.value = true
  try {
    await rejectCard(card.id, AUTHOR, rejectNote.value)
    showRejectInput.value = false
    rejectNote.value = ''
    await Promise.all([loadAcceptCard(), store.refreshTopicRow(props.topicId)])
  } catch (e) {
    store.reportError(e, '退回失败')
  } finally {
    acceptBusy.value = false
  }
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
  else if (resource === 'accept') void loadAcceptCard()
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
    void loadAcceptCard()
  } else if (resource === 'doc') {
    // B1 Phase 2: highlight the exact paragraphs this turn changed (falls back to
    // a whole-doc pulse when the turn's blocks aren't tagged). Leaving focus mode
    // re-renders the editor, which recreates its DOM — wait for that render to
    // settle before highlightTurn tags + flashes, or the flash is wiped instantly.
    focusMode.value = false
    await nextTick()
    if (turnId) docRef.value?.highlightTurn(turnId)
    else docRef.value?.pulse()
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
    void loadAcceptCard()
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
    void loadAcceptCard()
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
      <div class="panes d-flex flex-grow-1" style="min-width: 0; min-height: 0; position: relative">
        <!-- 群聊感 (fusion-design §3): the topic's member roster as a COMPACT
             avatar-stack, overlaid at the top-right of the chat column's header
             row (not a full-width bar) so the chat header lines up with the doc
             header. Not on the root topic (项目本体). -->
        <div
          v-if="selectedTopic.kind !== 'root' && !focusMode"
          class="topic-members-slot"
          :style="{ right: `calc(${100 - store.chatPct}% + 92px)` }"
        >
          <TopicMembers :topic-id="selectedTopic.id" :project-members="store.members" :me="AUTHOR" />
        </div>
        <ChatPanel
          v-show="!focusMode"
          ref="chatRef"
          class="col col-chat"
          :style="{ flex: `0 0 ${store.chatPct}%` }"
          :topic="selectedTopic"
          :pr-header="selectedTopic.kind !== 'root'"
          :members="store.members"
          :topic-list="store.topics"
          @turn-done="handleTurnDone"
          @tool-used="handleToolUsed"
          @state-changed="handleStateChanged"
          @mention-click="handleMentionClick"
          @open-file="(p: string) => docRef?.openFile?.(p)"
          @open-resource="handleOpenResource"
          @upgrade-message="handleUpgradeMessage"
          @open-topic="openTopic"
        >
          <!-- 成果待采纳框，放在对话时间线末尾 (GitHub PR 的合并框样式) -->
          <template
            v-if="pendingCard || gateCard || deliveringCard || (selectedTopic.status === 'archived' && acceptedCard)"
            #timeline-end
          >
            <!-- 机器闸门 (eval C2): the platform is running the project's
                 质量检查 in this topic's workspace — the card reaches the
                 reviewer only when it's green. -->
            <v-card v-if="gateCard && gateCard.status === 'pending_gate'" variant="outlined" class="merge-box mt-2">
              <div class="merge-box__bar" />
              <div class="pa-3">
                <div class="d-flex align-center ga-2 mb-1">
                  <v-progress-circular indeterminate size="18" width="2" />
                  <span class="t-title">平台检查进行中…</span>
                </div>
                <div class="text-caption text-medium-emphasis">
                  正在这个话题的工作区里运行项目配置的质量检查，通过后验收卡才会送给
                  <strong>@{{ gateCard.reviewer_handle }}</strong
                  >。
                </div>
              </div>
            </v-card>

            <!-- 闸门未过：卡片作废，芝士已被通知去修，修完会重新递卡。 -->
            <v-card v-else-if="gateCard && gateCard.status === 'gate_failed'" variant="outlined" class="merge-box mt-2">
              <div class="merge-box__bar" />
              <div class="pa-3">
                <div class="d-flex align-center ga-2 mb-1">
                  <v-icon color="error" size="19">mdi-close-octagon-outline</v-icon>
                  <span class="t-title">平台检查未通过</span>
                </div>
                <div class="text-caption text-medium-emphasis mb-2">
                  这张验收卡没有送出。芝士已收到检查结果，会修复后重新提交。
                </div>
                <v-btn
                  size="small"
                  variant="text"
                  :prepend-icon="showGateOutput ? 'mdi-chevron-up' : 'mdi-chevron-down'"
                  @click="showGateOutput = !showGateOutput"
                >
                  {{ showGateOutput ? '收起输出' : '查看输出' }}
                </v-btn>
                <pre v-if="showGateOutput" class="gate-output mt-2">{{ gateCard.gate_output || '（无输出）' }}</pre>
              </div>
            </v-card>

            <!-- 闸门没跑成：检查本身没能在门禁容器里跑起来，对代码没有结论。
                 刻意跟「未通过」分开显示——它是需要人看一眼的状态，不是代码红了。 -->
            <v-card
              v-else-if="gateCard && gateCard.status === 'gate_blocked'"
              variant="outlined"
              class="merge-box mt-2"
            >
              <div class="merge-box__bar" />
              <div class="pa-3">
                <div class="d-flex align-center ga-2 mb-1">
                  <v-icon color="warning" size="19">mdi-help-circle-outline</v-icon>
                  <span class="t-title">平台检查未能执行</span>
                </div>
                <div class="text-caption text-medium-emphasis mb-2">
                  检查程序没能启动，所以它对这次改动<strong>没有结论</strong>（既不是通过也不是未通过）。
                  这张验收卡没有送出。芝士已收到通知，会先恢复检查环境再重新提交；如果反复启动失败，需要人工介入。
                </div>
                <v-btn
                  size="small"
                  variant="text"
                  :prepend-icon="showGateOutput ? 'mdi-chevron-up' : 'mdi-chevron-down'"
                  @click="showGateOutput = !showGateOutput"
                >
                  {{ showGateOutput ? '收起输出' : '查看输出' }}
                </v-btn>
                <pre v-if="showGateOutput" class="gate-output mt-2">{{ gateCard.gate_output || '（无输出）' }}</pre>
              </div>
            </v-card>

            <v-card v-else-if="pendingCard" variant="outlined" class="merge-box mt-2">
              <div class="merge-box__bar" />
              <div class="pa-3">
                <div class="d-flex align-center ga-2 mb-1">
                  <v-icon :color="pendingCard.status === 'conflict' ? 'warning' : 'success'" size="19">
                    mdi-source-merge
                  </v-icon>
                  <span class="t-title">
                    {{ pendingCard.status === 'conflict' ? '合并冲突 · 芝士处理中' : '成果待采纳' }}
                  </span>
                </div>
                <div v-if="pendingCard.status === 'conflict'" class="text-caption text-medium-emphasis mb-2">
                  {{ pendingCard.note || '采纳时发生合并冲突，芝士正在工作区里解决。' }}
                  它在对话里汇报完成后即可重试。
                </div>
                <div class="d-flex align-center flex-wrap ga-1 text-body-2 mb-1">
                  <span>等</span>
                  <strong>@{{ pendingCard.reviewer_handle }}</strong>
                  <span>验收</span>
                  <!-- 改验收人 (spec §4.4): 任何成员都可以改推荐/加人 -->
                  <v-menu>
                    <template #activator="{ props: menuProps }">
                      <v-btn
                        v-bind="menuProps"
                        size="x-small"
                        variant="text"
                        density="comfortable"
                        class="text-medium-emphasis"
                        :disabled="acceptBusy"
                      >
                        改派
                      </v-btn>
                    </template>
                    <v-list density="compact">
                      <v-list-subheader>改派验收人</v-list-subheader>
                      <v-list-item
                        v-for="mbr in store.members"
                        :key="mbr.user_handle"
                        :active="mbr.user_handle === pendingCard.reviewer_handle"
                        @click="onReassignCard(mbr.user_handle)"
                      >
                        <v-list-item-title class="text-body-2"> @{{ mbr.user_handle }} </v-list-item-title>
                        <v-list-item-subtitle class="text-caption">
                          {{ mbr.role }}
                        </v-list-item-subtitle>
                      </v-list-item>
                      <v-list-item v-if="store.members.length === 0">
                        <v-list-item-title class="text-caption text-medium-emphasis"> 暂无可选成员 </v-list-item-title>
                      </v-list-item>
                    </v-list>
                  </v-menu>
                </div>
                <div v-if="pendingCard.routing_reason" class="text-caption text-medium-emphasis mb-3">
                  推荐理由：{{ pendingCard.routing_reason }}
                </div>
                <!--
                  提交与 PR 规范: 采纳会把整个分支压成一个提交，标题就是这一行。
                  采纳前是最后一次能反对它的机会，所以它必须在按钮上方可见，而不是
                  等它进了 git 历史才有人发现写的是话题标题。
                -->
                <div v-if="pendingCard.change_subject" class="mb-3">
                  <div class="text-caption text-medium-emphasis">合并后的提交标题</div>
                  <code class="text-caption">{{ pendingCard.change_subject }}</code>
                </div>
                <!--
                  机器闸门 (eval C2) + 人类授权动作前移 (2026-08-10): 闸门跑的是
                  check.sh --no-tests——lint 和类型，没有测试。真 CI 只在 PR 上跑，
                  而 PR 是你点下去之后才开的。所以这一格绝不能是绿勾：那等于让卡面
                  替一段还没被任何测试碰过的代码背书。它说的是"即将开始跑"。
                -->
                <div
                  v-if="pendingCard.gate_passed_at"
                  class="d-flex align-center ga-1 text-caption text-medium-emphasis mb-2"
                >
                  <v-icon size="15">mdi-timer-sand</v-icon>
                  平台检查已通过（只跑了 lint 和类型检查，没有跑测试）· 完整 CI 在你授权后才开始
                </div>
                <!-- 采纳 PR 化 (#188 §5.1): the real PR + its CI, live. -->
                <div v-if="pendingCard.pr_url" class="mb-2">
                  <div class="d-flex align-center flex-wrap ga-2">
                    <v-chip
                      size="small"
                      variant="tonal"
                      prepend-icon="mdi-source-pull"
                      :href="pendingCard.pr_url"
                      target="_blank"
                    >
                      PR #{{ pendingCard.pr_number }}
                    </v-chip>
                    <span v-if="prChecks?.available && prChecks.mergeable === false" class="text-caption text-error">
                      与主分支冲突
                    </span>
                  </div>
                  <div
                    v-for="chk in prChecks?.checks ?? []"
                    :key="chk.name"
                    class="d-flex align-center ga-1 text-caption text-medium-emphasis mt-1"
                  >
                    <v-icon
                      size="14"
                      :color="
                        chk.conclusion === 'success' ? 'success' : chk.conclusion === 'failure' ? 'error' : undefined
                      "
                    >
                      {{
                        chk.conclusion === 'success'
                          ? 'mdi-check-circle'
                          : chk.conclusion === 'failure'
                            ? 'mdi-close-circle'
                            : 'mdi-progress-clock'
                      }}
                    </v-icon>
                    {{ chk.name }}
                    <span v-if="chk.status !== 'completed'">（进行中）</span>
                  </div>
                </div>
                <!-- 主分支保护 (spec §4.4): N 人批准后采纳才会真正合入。 -->
                <div v-if="pendingCard.approvals_required > 1" class="d-flex align-center flex-wrap ga-2 mb-3">
                  <v-chip
                    size="small"
                    variant="tonal"
                    :color="pendingCard.approvals.length >= pendingCard.approvals_required ? 'success' : undefined"
                    prepend-icon="mdi-account-check-outline"
                  >
                    {{ pendingCard.approvals.length }}/{{ pendingCard.approvals_required }}
                    已批准
                  </v-chip>
                  <span v-if="pendingCard.approvals.length" class="text-caption text-medium-emphasis">
                    {{ pendingCard.approvals.map((h) => '@' + h).join('、') }}
                  </span>
                  <v-btn
                    v-if="!pendingCard.approvals.includes(AUTHOR)"
                    size="small"
                    variant="outlined"
                    class="btn-secondary"
                    :disabled="acceptBusy"
                    prepend-icon="mdi-thumb-up-outline"
                    @click="onApproveCard"
                  >
                    批准
                  </v-btn>
                  <span v-else class="d-inline-flex align-center ga-1 text-caption text-medium-emphasis">
                    <v-icon size="14">mdi-check</v-icon>你已批准
                  </span>
                </div>
                <div class="d-flex align-center ga-2">
                  <v-btn
                    color="success"
                    variant="flat"
                    :loading="acceptBusy"
                    :disabled="acceptBusy"
                    prepend-icon="mdi-check"
                    @click="onAcceptCard"
                  >
                    {{ pendingCard.status === 'conflict' ? '重试采纳' : '采纳并归档' }}
                  </v-btn>
                  <v-btn
                    variant="text"
                    :disabled="acceptBusy"
                    prepend-icon="mdi-undo"
                    @click="showRejectInput = !showRejectInput"
                  >
                    退回
                  </v-btn>
                </div>
                <div v-if="showRejectInput" class="d-flex align-end ga-2 mt-3">
                  <v-text-field
                    v-model="rejectNote"
                    variant="outlined"
                    density="compact"
                    hide-details
                    placeholder="退回说明（可选）"
                    class="flex-grow-1"
                  />
                  <v-btn variant="outlined" class="btn-secondary" :loading="acceptBusy" @click="onRejectCard">
                    确认退回
                  </v-btn>
                </div>
              </div>
            </v-card>

            <!-- 交付进度: 人已经点过采纳，剩下的（检查、合并——具体几步由项目的
                 forge 决定，后端下发）是机器在跑，要跑几小时。只读，不给任何按钮
                 —— 授权已经给过了，不该再问人第二次。 -->
            <v-card v-else-if="deliveringCard" variant="outlined" class="merge-box mt-2">
              <div class="merge-box__bar" />
              <div class="pa-3">
                <div class="d-flex align-center ga-2 mb-1">
                  <v-progress-circular indeterminate size="18" width="2" />
                  <span class="t-title">交付中 · {{ deliveryStage?.title }}</span>
                </div>
                <div class="text-caption text-medium-emphasis mb-2">
                  已由 <strong>@{{ deliveringCard.decided_by }}</strong> 采纳，{{ deliveryStage?.hint }}
                </div>
                <!-- 阶段条：人点完之后走到哪一步了 -->
                <div class="d-flex align-center flex-wrap ga-1 text-caption mb-2">
                  <template v-for="(step, i) in deliveryStage?.steps ?? []" :key="step.key">
                    <v-icon v-if="i > 0" size="13" class="text-disabled">mdi-chevron-right</v-icon>
                    <span
                      class="d-flex align-center ga-1"
                      :class="step.state === 'todo' ? 'text-disabled' : 'text-medium-emphasis'"
                    >
                      <v-progress-circular v-if="step.state === 'active'" indeterminate size="13" width="2" />
                      <v-icon v-else-if="step.state === 'done'" color="success" size="14">mdi-check-circle</v-icon>
                      <v-icon v-else size="14">mdi-circle-outline</v-icon>
                      {{ step.label }}
                    </span>
                  </template>
                </div>
                <!-- 后端把故障写在卡的 note 上，这是它唯一露头的地方。 -->
                <div
                  v-if="deliveryNote"
                  class="text-caption mb-2"
                  :class="deliveryNote.tone === 'error' ? 'text-error' : 'text-medium-emphasis'"
                >
                  {{ deliveryNote.text }}
                </div>
                <!-- PR + 实时 CI，复用待采纳卡那套 prChecks 轮询。 -->
                <div v-if="deliveringCard.pr_url">
                  <div class="d-flex align-center flex-wrap ga-2">
                    <v-chip
                      size="small"
                      variant="tonal"
                      prepend-icon="mdi-source-pull"
                      :href="deliveringCard.pr_url"
                      target="_blank"
                    >
                      PR #{{ deliveringCard.pr_number }}
                    </v-chip>
                    <span v-if="deliveringCard.pr_head_sha" class="text-caption text-medium-emphasis">
                      {{ deliveringCard.pr_head_sha.slice(0, 7) }}
                    </span>
                    <span v-if="prChecks?.available && prChecks.mergeable === false" class="text-caption text-error">
                      与主分支冲突
                    </span>
                  </div>
                  <div
                    v-for="chk in prChecks?.checks ?? []"
                    :key="chk.name"
                    class="d-flex align-center ga-1 text-caption text-medium-emphasis mt-1"
                  >
                    <v-icon
                      size="14"
                      :color="
                        chk.conclusion === 'success' ? 'success' : chk.conclusion === 'failure' ? 'error' : undefined
                      "
                    >
                      {{
                        chk.conclusion === 'success'
                          ? 'mdi-check-circle'
                          : chk.conclusion === 'failure'
                            ? 'mdi-close-circle'
                            : 'mdi-progress-clock'
                      }}
                    </v-icon>
                    {{ chk.name }}
                    <span v-if="chk.status !== 'completed'">（进行中）</span>
                  </div>
                </div>
                <!--
                  人工放行：明知检查没全绿仍合并。平台自己永远不走这条路——红着合
                  有时候是对的（CI 抽风、与本次改动无关的既有失败），不能接受的是
                  没有人做过这个决定。所以它默认收起、要填理由，点下去在卡上留名。
                -->
                <div class="mt-3">
                  <v-btn
                    v-if="!showForceMergeInput"
                    size="small"
                    variant="text"
                    class="text-medium-emphasis"
                    prepend-icon="mdi-alert-decagram-outline"
                    @click="showForceMergeInput = true"
                  >
                    人工放行并合并
                  </v-btn>
                  <template v-else>
                    <div class="text-caption text-medium-emphasis mb-1">
                      在检查未全部通过的情况下强制合并。平台会记录操作人、时间和当时的检查状态。
                    </div>
                    <v-textarea
                      v-model="forceMergeReason"
                      label="理由"
                      rows="2"
                      auto-grow
                      density="compact"
                      variant="outlined"
                      hide-details
                      class="mb-2"
                    />
                    <div class="d-flex ga-2">
                      <v-btn
                        size="small"
                        color="warning"
                        variant="flat"
                        :loading="acceptBusy"
                        :disabled="acceptBusy"
                        @click="onForceMerge"
                      >
                        确认放行并合并
                      </v-btn>
                      <v-btn size="small" variant="text" :disabled="acceptBusy" @click="showForceMergeInput = false">
                        取消
                      </v-btn>
                    </div>
                  </template>
                </div>
              </div>
            </v-card>

            <!-- Archived (accepted) topic: 采纳可撤销 (spec §6.3). -->
            <v-card v-else-if="acceptedCard" variant="outlined" class="merge-box mt-2">
              <div class="merge-box__bar" />
              <div class="pa-3">
                <div class="d-flex align-center ga-2 mb-1">
                  <v-icon color="success" size="19">mdi-check-circle-outline</v-icon>
                  <span class="t-title">已采纳并归档</span>
                </div>
                <div class="text-body-2 c-muted mb-3">
                  由 <strong>@{{ acceptedCard.decided_by }}</strong> 采纳
                </div>
                <v-btn
                  variant="outlined"
                  class="btn-secondary"
                  :loading="acceptBusy"
                  :disabled="acceptBusy"
                  prepend-icon="mdi-undo"
                  @click="onRevokeCard"
                >
                  撤回采纳
                </v-btn>
              </div>
            </v-card>
          </template>
        </ChatPanel>
        <div
          v-if="!focusMode"
          class="pane-resizer"
          title="拖动调整宽度，双击复位"
          @mousedown.prevent="startPaneDrag"
          @dblclick="store.setChatPct(50)"
        />
        <DocPanel
          ref="docRef"
          class="col col-doc"
          :style="{ flex: '1 1 0', minWidth: 0 }"
          :topic="selectedTopic"
          :activity-tick="activityTick"
          :worklog="worklog"
          :working="working"
          :working-since="workingSince"
          :focus="focusMode"
          :topic-list="store.topics"
          @toggle-focus="focusMode = !focusMode"
          @open-topic="openTopic"
          @topics-changed="store.refreshTopics"
          @comment-intent="onCommentIntent"
        />
      </div>

      <!-- 输入栏 spans 对话 + 文档, not the rail (spec §7.1) -->
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
}
/* GitHub-PR-style merge box — green (the merge convention) stays. */
.merge-box {
  position: relative;
  overflow: hidden;
  border-color: var(--ok) !important;
}
.merge-box__bar {
  position: absolute;
  inset: 0 auto 0 0;
  width: 3px;
  background: var(--ok);
}
/* 机器闸门: tail of the failed check's output (查看输出). */
.gate-output {
  max-height: 240px;
  overflow: auto;
  padding: 8px 10px;
  border-radius: 6px;
  background: var(--fill);
  font-family: var(--mono, ui-monospace, monospace);
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
}
.col {
  min-width: 0;
}
/* Draggable splitter between chat and doc (replaces the static divider). */
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
  padding: 5px 8px 5px 10px;
  border-left: 2px solid rgb(var(--v-theme-primary));
  /* 只圆右侧两角。写成 `0 8px 8px 0` 的简写形式过不了圆角阶梯检查（它逐值比对），
     所以拆成长写法 —— 视觉完全一致。 */
  border-top-right-radius: var(--radius-md);
  border-bottom-right-radius: var(--radius-md);
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

/* 群聊感 (fusion-design §3): compact roster indicator overlaid at the top-right
   of the chat column's header row (pr-header), so the chat header and the doc
   header line up. Span the full pr-header row and flex-center so the members
   count shares a vertical center with the 进行中/已归档 status badge instead of
   riding on a magic top offset (which read as misaligned). The pr-header row is
   the 15px/1.4 title (~21px) plus py-3 (12px×2) padding = ~45px tall. */
.topic-members-slot {
  position: absolute;
  top: 0;
  height: 45px;
  z-index: 3;
  display: flex;
  align-items: center;
}

/* Secondary button: 1px border, neutral text, surface bg. */
.btn-secondary {
  border: 1px solid var(--line-2);
  color: var(--text);
  background: var(--surface);
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
