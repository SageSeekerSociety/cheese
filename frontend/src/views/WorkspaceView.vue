<script setup lang="ts">
import { myHandle } from '../me'
import { formatToolAction, isPlatformAction, toolLabel } from '../lib/toolLabels'
import { refreshBlockCache } from '../lib/blockCache'
import {
  computed,
  inject,
  nextTick,
  onMounted,
  onUnmounted,
  ref,
  watch,
  type Ref,
} from 'vue'
import { useRoute, useRouter } from 'vue-router'
import ChatPanel from '../components/ChatPanel.vue'
import DocPanel from '../components/DocPanel.vue'
import ProjectDocsView from './ProjectDocsView.vue'
import TopicSidebar from '../components/TopicSidebar.vue'
import {
  acceptCard,
  addComment,
  archiveTopic,
  attachmentRawUrl,
  createProject,
  createTopic,
  getAcceptCards,
  getPrivateChat,
  getTopic,
  getTopicUnread,
  listProjectMembers,
  listProjects,
  listTopics,
  markTopicRead,
  reassignCard,
  rejectCard,
  revokeCard,
  splitTopic,
  unarchiveTopic,
  upgradeBlock,
} from '../api'
import { usePendingAttachments } from '../lib/attachments'
import type {
  AcceptCard,
  ChatAttachment,
  Project,
  ProjectMemberRow,
  Topic,
} from '../types'

// projectId comes from the route (/project/:projectId). When absent we fall
// back to the first project so 工作台 is never empty.
// Named so App.vue's <keep-alive include> can pin this view.
defineOptions({ name: 'WorkspaceView' })

const props = defineProps<{ projectId?: string }>()
const router = useRouter()
const route = useRoute()

// ---- Resizable layout: rail width + chat/doc split, persisted across sessions.
const railWidth = ref(280)
const chatPct = ref(50)
try {
  const saved = JSON.parse(localStorage.getItem('cheesex.layout') || '{}')
  if (typeof saved.railWidth === 'number') railWidth.value = saved.railWidth
  if (typeof saved.chatPct === 'number') chatPct.value = saved.chatPct
} catch {
  // ignore malformed stored layout
}
// 专注模式 (spec §7.1): hide the chat column so the document spans the whole
// workspace. Session-only (intentionally not persisted — it's a transient mode).
const focusMode = ref(false)
const docRef = ref<{
  pulse: () => void
  highlightTurn: (turnId: string) => void
  openFile?: (path: string) => void
  refreshComments?: () => Promise<void>
} | null>(null)
const clampNum = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))
watch([railWidth, chatPct], () => {
  localStorage.setItem(
    'cheesex.layout',
    JSON.stringify({ railWidth: railWidth.value, chatPct: chatPct.value }),
  )
})
function onRailWidth(w: number) {
  railWidth.value = clampNum(w, 190, 480)
}
// Drag the chat|doc splitter: set chat's width as a % of the panes row.
function startPaneDrag(e: MouseEvent) {
  const panes = (e.currentTarget as HTMLElement).parentElement
  if (!panes) return
  const rect = panes.getBoundingClientRect()
  const move = (ev: MouseEvent) => {
    chatPct.value = clampNum(((ev.clientX - rect.left) / rect.width) * 100, 25, 80)
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

const AUTHOR = myHandle()

const projects = ref<Project[]>([])
const topics = ref<Topic[]>([])
const selectedProjectId = ref<string | null>(null)
const selectedTopicId = ref<string | null>(null)
const loadingTopics = ref(false)
const globalError = ref<string | null>(null)

// Bumped on every AI turn / tool use. Watched by DocPanel (reload the living
// doc 芝士 maintained) and used here to refresh the topic tree so new
// sub-topics appear (spec §7.1 实时联动).
const activityTick = ref(0)

const selectedTopic = computed<Topic | null>(
  () => topics.value.find((t) => t.id === selectedTopicId.value) ?? null,
)

// ---- 私聊 (飞书私聊): 1:1 chat with 芝士 in the main area ----
// The main area shows either a work topic ('topic') or the private chat
// ('private'). 飞书 convention: a conversation in the left list opens as a
// normal chat in the main area — no document, no PR header, no accept box.
const mode = ref<'topic' | 'private' | 'docs'>('topic')
// 项目文档 shown in the main area (keeping the rail) instead of a separate page.
const docKind = ref<'charter' | 'decisions' | 'weeklies' | 'memory'>('charter')
function selectDocs(kind: 'charter' | 'decisions' | 'weeklies' | 'memory') {
  if (!selectedProjectId.value) return
  mode.value = 'docs'
  docKind.value = kind
}
const privateTopic = ref<Topic | null>(null)
const privateLoading = ref(false)
const privateError = ref<string | null>(null)

async function selectPrivate() {
  const pid = selectedProjectId.value
  if (!pid) return
  mode.value = 'private'
  worklog.value = []
  // Re-fetch so we always have the right project's private topic.
  privateLoading.value = true
  privateError.value = null
  privateTopic.value = null
  try {
    const topic = await getPrivateChat(pid, AUTHOR)
    if (selectedProjectId.value === pid && mode.value === 'private') {
      privateTopic.value = topic
    }
  } catch (e) {
    if (mode.value === 'private') {
      privateError.value = e instanceof Error ? e.message : '打开私聊失败'
    }
  } finally {
    if (selectedProjectId.value === pid) privateLoading.value = false
  }
}

// Snackbar v-model bridge: visible while there's an error; writing false clears.
const hasError = computed<boolean>({
  get: () => globalError.value !== null,
  set: (v) => {
    if (!v) globalError.value = null
  },
})

// ---- Spanning composer (spec §7.1: 输入栏在对话+文档区域底部) ----
const chatRef = ref<{
  send: (
    content: string,
    summon: boolean,
    attachments?: ChatAttachment[],
  ) => boolean
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
    globalError.value = msg
  },
)
function pickAttFiles() {
  composerFileInput.value?.click()
}
function onAttFilePicked(e: Event) {
  const input = e.target as HTMLInputElement
  if (input.files?.length) void addAttFiles(input.files)
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
// message. A quote chip above the input shows the target; Esc/✕ exits. ----
const commentIntent = ref<{ anchorId: string | null; quote: string } | null>(null)
const commentSending = ref(false)
const composerInput = ref<{ focus?: () => void } | null>(null)

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
      await addComment(
        tid,
        text,
        AUTHOR,
        commentIntent.value.anchorId ?? undefined,
        commentIntent.value.quote,
      )
      draft.value = ''
      commentIntent.value = null
      void docRef.value?.refreshComments?.()
    } catch (e) {
      reportError(e, '评论失败')
    } finally {
      commentSending.value = false
    }
    return
  }
  const ok = chatRef.value?.send(
    expandMentions(draft.value),
    summon.value,
    pendingAtts.value.slice(),
  )
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
  kind: 'member' | 'topic'
  // Secondary line: @handle for people, status for topics.
  sub: string
  agent: boolean
}
const mentionMatches = computed<MentionItem[]>(() => {
  const q = mentionQuery.value
  if (q === null) return []
  const items: MentionItem[] = [
    ...projectMembers.value.map((m) => ({
      label: m.name || m.user_handle,
      kind: 'member' as const,
      sub: `@${m.user_handle}`,
      agent: m.user_handle === 'cheese',
    })),
    ...topics.value
      .filter((t) => t.kind !== 'root')
      .map((t) => ({
        label: t.title,
        kind: 'topic' as const,
        sub: t.status === 'archived' ? '已归档' : '进行中',
        agent: false,
      })),
  ]
  const ql = q.toLowerCase()
  return items
    .filter((i) => i.label.toLowerCase().includes(ql))
    .slice(0, 6)
})
function pickMention(label: string) {
  draft.value = draft.value.replace(/@([^\s@]*)$/, `@${label} `)
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
  return (
    composing ||
    e.isComposing ||
    e.keyCode === 229 ||
    e.timeStamp - compositionEndedAt < 100
  )
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
    pickMention(mentionMatches.value[0].label)
    return
  }
  void sendDraft()
}

function reportError(e: unknown, fallback: string) {
  globalError.value = e instanceof Error ? e.message : fallback
}

async function refreshProjects() {
  try {
    const payload = await listProjects()
    projects.value = payload.data
  } catch (e) {
    reportError(e, '加载项目失败')
  }
}

async function loadTopicsFor(id: string) {
  selectedProjectId.value = id
  selectedTopicId.value = null
  topics.value = []
  // Switching project resets the main area back to the topic view; drop any
  // stale private topic so 私聊 re-fetches for the new project on next open.
  mode.value = 'topic'
  privateTopic.value = null
  loadingTopics.value = true
  loadProjectMembers()
  try {
    const payload = await listTopics(id)
    if (selectedProjectId.value !== id) return
    topics.value = payload.data
    // A ?topic=<id> in the URL (from 来自话题 / member links) pre-selects that
    // topic; otherwise default to the root topic (本体/大本营), the coordination
    // hub shown when you open a project (spec §6/§7.1).
    const wanted = route.query.topic ? String(route.query.topic) : null
    if (wanted && topics.value.some((t) => t.id === wanted)) {
      mode.value = 'topic'
      selectedTopicId.value = wanted
    } else if (!selectedTopicId.value) {
      const root = topics.value.find((t) => t.kind === 'root')
      if (root) selectedTopicId.value = root.id
    }
  } catch (e) {
    reportError(e, '加载话题失败')
  } finally {
    if (selectedProjectId.value === id) loadingTopics.value = false
  }
  refreshUnread()
}

// Navigate so the URL carries the project; the route watcher below loads it.
function selectProject(id: string) {
  if (!id || id === selectedProjectId.value) return
  router.push({ name: 'workspace-project', params: { projectId: id } })
}

function selectTopic(id: string) {
  // Selecting a work topic switches back to the normal topic+doc view.
  mode.value = 'topic'
  const previous = selectedTopicId.value
  selectedTopicId.value = id
  worklog.value = []
  // Keep the URL's ?topic= in sync with what's open, so refresh, share, and
  // back-navigation land on this topic. Topic→topic changes PUSH a history
  // entry — jumping into a topic via a #chip must be walkable back with the
  // browser's back button ("跳转进一个话题后怎么返回"). The very first
  // selection (page load picking a default) replaces instead, so back doesn't
  // dead-end on a bare project URL. Guard avoids a redundant navigation when
  // the query already matches (e.g. this call came from the route watcher).
  const pid = selectedProjectId.value ?? props.projectId
  if (pid && route.query.topic !== id) {
    const nav = previous && previous !== id ? router.push : router.replace
    nav({
      name: 'workspace-project',
      params: { projectId: pid },
      query: { topic: id },
    })
  }
}

// ---- 采纳卡 (eval C5/A3): a banner above the composer ----
const acceptCards = ref<AcceptCard[]>([])
const acceptBusy = ref(false)
const rejectNote = ref('')
const showRejectInput = ref(false)

// Newest pending card (the list comes newest-first). A card in `conflict`
// (采纳时合并冲突，芝士被派去解决) keeps the merge box up — as a STATE, with a
// retry button — instead of pretending the accept went through.
const pendingCard = computed<AcceptCard | null>(
  () =>
    acceptCards.value.find(
      (c) => c.status === 'pending' || c.status === 'conflict',
    ) ?? null,
)
// The accepted card on an archived topic — its presence lets us offer 撤回采纳.
const acceptedCard = computed<AcceptCard | null>(
  () => acceptCards.value.find((c) => c.status === 'accepted') ?? null,
)

// ---- 改验收人 (spec §4.4): project members for the reassign menu ----
const projectMembers = ref<ProjectMemberRow[]>([])

async function loadProjectMembers() {
  const pid = selectedProjectId.value
  if (!pid) return
  try {
    const payload = await listProjectMembers(pid)
    if (selectedProjectId.value === pid) projectMembers.value = payload.data
  } catch {
    // Best-effort; the 改 menu just stays empty.
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
    reportError(e, '改验收人失败')
  } finally {
    acceptBusy.value = false
  }
}

async function loadAcceptCard() {
  acceptCards.value = []
  showRejectInput.value = false
  rejectNote.value = ''
  const tid = selectedTopicId.value
  if (!tid) return
  try {
    const payload = await getAcceptCards(tid)
    if (selectedTopicId.value === tid) acceptCards.value = payload.data
  } catch {
    // Best-effort; the banner just stays hidden.
  }
}

// After a decision, the topic flips to archived — refetch it and patch the list
// so the header chip updates.
async function refreshSelectedTopic() {
  const pid = selectedProjectId.value
  const tid = selectedTopicId.value
  if (!pid || !tid) return
  try {
    const fresh = await getTopic(pid, tid)
    if (!fresh) return
    const i = topics.value.findIndex((t) => t.id === tid)
    if (i >= 0) topics.value[i] = fresh
  } catch {
    // ignore
  }
}

async function onAcceptCard() {
  const card = pendingCard.value
  if (!card) return
  acceptBusy.value = true
  try {
    const updated = await acceptCard(card.id, AUTHOR)
    if (updated.status === 'conflict') {
      globalError.value =
        '合并冲突，这次没有归档——芝士已被派去解决，它汇报后再点「重试采纳」。'
    }
    await Promise.all([loadAcceptCard(), refreshSelectedTopic()])
  } catch (e) {
    reportError(e, '采纳失败')
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
    await Promise.all([loadAcceptCard(), refreshSelectedTopic()])
  } catch (e) {
    reportError(e, '撤回采纳失败')
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
    await Promise.all([loadAcceptCard(), refreshSelectedTopic()])
  } catch (e) {
    reportError(e, '退回失败')
  } finally {
    acceptBusy.value = false
  }
}

// ---- 话题级未读 (Feishu-style badges, spec 前端体验优化 A) ----
const unreadMap = ref<Record<string, number>>({})

async function refreshUnread() {
  const pid = selectedProjectId.value
  if (!pid || !AUTHOR) return
  try {
    const map = await getTopicUnread(pid, AUTHOR)
    if (selectedProjectId.value !== pid) return
    // The open topic is being read right now — its badge never shows.
    if (selectedTopicId.value) delete map[selectedTopicId.value]
    // Background-refresh the timeline cache of topics whose unread count grew:
    // by the time the user switches back, the reply that landed while they
    // were away is already rendered on the first frame (no late pop-in).
    for (const [tid, n] of Object.entries(map)) {
      if (n > (unreadMap.value[tid] ?? 0)) void refreshBlockCache(tid)
    }
    unreadMap.value = map
  } catch {
    // Best-effort; badges just stay as they were.
  }
}

// Opening a topic = reading it: bump the server-side cursor and clear the
// badge locally (optimistic — the next refresh agrees).
function markSelectedRead(id: string) {
  if (!AUTHOR) return
  if (unreadMap.value[id] !== undefined) {
    const next = { ...unreadMap.value }
    delete next[id]
    unreadMap.value = next
  }
  markTopicRead(id, AUTHOR).catch(() => {})
}

// ---- 归档去向: manual archive / unarchive from the sidebar ----
async function handleArchiveTopic(id: string) {
  try {
    await archiveTopic(id, AUTHOR)
    // Archiving the topic you're looking at closes it — the main panel
    // returns to the empty state instead of showing a frozen archive.
    if (selectedTopicId.value === id) selectedTopicId.value = null
    await refreshTopics()
  } catch (e) {
    reportError(e, '归档失败')
  }
}

async function handleUnarchiveTopic(id: string) {
  try {
    await unarchiveTopic(id, AUTHOR)
    await refreshTopics()
  } catch (e) {
    reportError(e, '取消归档失败')
  }
}

// Silent refresh of the current project's topic list (no spinner / selection
// reset), so newly created sub-topics show up after 芝士 acts.
async function refreshTopics() {
  const id = selectedProjectId.value
  if (!id) return
  try {
    const payload = await listTopics(id)
    if (selectedProjectId.value === id) topics.value = payload.data
  } catch {
    // Best-effort background refresh; ignore.
  }
}

function handleTurnDone() {
  // The live feed's job is over — the persisted 现场 transcript is the record.
  working.value = false
  workingSince.value = null
  worklog.value = []
  activityTick.value += 1
  refreshTopics()
  // 芝士's reply landed after our read cursor — the user is watching this
  // topic, so re-bump the cursor before refreshing badges (other topics that
  // got messages in the background DO light up).
  if (selectedTopicId.value) markSelectedRead(selectedTopicId.value)
  refreshUnread()
}

// A `cheese <sub>` command changed a platform resource mid-turn (it runs as Bash,
// so we can't key off a tool name) — refresh the affected panel live (§3.1.1).
function handleStateChanged(resource: string) {
  if (resource === 'topics') refreshTopics()
  else if (resource === 'accept') loadAcceptCard()
  else activityTick.value += 1 // doc / decision / milestone / notify → reload
}

// An action card's button → open the relevant view (§3.1.1 控件).
async function handleOpenResource(resource: string, turnId?: string) {
  const pid = selectedProjectId.value
  if (!pid) return
  if (resource === 'decision') {
    router.push({ name: 'project-decisions', params: { projectId: pid } })
  } else if (resource === 'milestone') {
    router.push({ name: 'calendar', params: { projectId: pid } })
  } else if (resource === 'accept') {
    loadAcceptCard()
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
  if (selectedProjectId.value) {
    router.push({
      name: 'member',
      params: { projectId: selectedProjectId.value, handle },
    })
  }
}

// Human composer: turn a friendly "@名字 / @话题名" into the canonical token
// (<@handle> for a teammate, <#topicId> for a topic) at send time — longest
// patterns first so substrings don't mis-match — same encoding 芝士 uses.
function expandMentions(text: string): string {
  const subs: { pat: string; token: string }[] = [
    // Both spellings a human naturally types: @名字 and @handle (e.g. a handle
    // pasted from someone else's message).
    ...projectMembers.value.flatMap((m) => [
      { pat: `@${m.name || m.user_handle}`, token: `<@${m.user_handle}>` },
      { pat: `@${m.user_handle}`, token: `<@${m.user_handle}>` },
    ]),
    ...topics.value.map((t) => ({ pat: `@${t.title}`, token: `<#${t.id}>` })),
  ].sort((a, b) => b.pat.length - a.pat.length)
  let out = text
  for (const s of subs) out = out.split(s.pat).join(s.token)
  return out
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
    refreshTopics()
  } else if (name === 'request_accept') {
    // 芝士 递出验收卡: refresh the banner so it shows up immediately.
    loadAcceptCard()
  }
}

async function handleCreateProject(name: string) {
  try {
    // The signed-in user owns what they create (drives testing-tier profile
    // permission checks and 谁能撤销采纳 etc.).
    const project = await createProject(name, AUTHOR)
    projects.value.push(project)
    selectProject(project.id)
  } catch (e) {
    reportError(e, '创建项目失败')
  }
}

async function handleCreateTopic(title: string) {
  const projectId = selectedProjectId.value
  if (!projectId) return
  try {
    // Untitled by default — the title is derived from the first message.
    const topic = await createTopic(projectId, title.trim() || '新话题')
    topics.value.push(topic)
    selectTopic(topic.id)
  } catch (e) {
    reportError(e, '创建话题失败')
  }
}

// ＋ 子话题 on a topic row: split off a sub-topic, refresh the tree, select it.
async function handleSplitTopic(payload: { topicId: string; title: string }) {
  try {
    const sub = await splitTopic(payload.topicId, payload.title, AUTHOR)
    await refreshTopics()
    selectTopic(sub.id)
  } catch (e) {
    reportError(e, '拆分子话题失败')
  }
}

// ⤴ 升级为话题 from a message bubble (eval A1).
async function handleUpgradeMessage(messageId: string) {
  try {
    const topic = await upgradeBlock(messageId, AUTHOR)
    await refreshTopics()
    selectTopic(topic.id)
  } catch (e) {
    reportError(e, '升级为话题失败')
  }
}

// Keep selection in sync with the route param. When no project is in the URL,
// default to the first available project (and reflect it in the URL).
watch(
  () => props.projectId,
  async (id) => {
    if (id) {
      if (id !== selectedProjectId.value) await loadTopicsFor(id)
      return
    }
    if (projects.value.length === 0) await refreshProjects()
    const first = projects.value[0]
    if (first) {
      router.replace({ name: 'workspace-project', params: { projectId: first.id } })
    }
  },
)

// Load the accept-card banner whenever the selected topic changes (covers
// sidebar selection, create/split/upgrade, and route-driven selection), and
// mark the newly opened topic read (its unread badge clears, Feishu-style).
watch(selectedTopicId, (id) => {
  loadAcceptCard()
  clearPendingAtts() // pending images belong to the topic they were typed in
  commentIntent.value = null // a comment target belongs to its topic's doc
  if (id) markSelectedRead(id)
})

// ?topic=<id> changing while already in the workspace (same project) — e.g. a
// 来自话题 link clicked from elsewhere — should re-select without a full reload.
watch(
  () => route.query.topic,
  (t) => {
    const wanted = t ? String(t) : null
    if (wanted && wanted !== selectedTopicId.value && topics.value.some((x) => x.id === wanted)) {
      selectTopic(wanted)
    }
  },
)

// 记一笔 (E1/E3): App.vue bumps this after ingesting an activity; refresh the
// topic tree so the new [活动] topic appears.
const activityBump = inject<Ref<number>>('activityBump')
if (activityBump) {
  watch(activityBump, () => {
    refreshTopics()
  })
}

// 实时性 (前端体验优化 C, MVP): poll unread badges so messages landing in
// OTHER topics light up without a manual refresh. 30s keeps it fresher than
// "only when I click around" without hammering the backend.
let unreadTimer: number | undefined

onMounted(async () => {
  await refreshProjects()
  if (props.projectId) {
    await loadTopicsFor(props.projectId)
  } else {
    const first = projects.value[0]
    if (first) {
      router.replace({ name: 'workspace-project', params: { projectId: first.id } })
    }
  }
  unreadTimer = window.setInterval(() => {
    refreshUnread()
  }, 30_000)
})

onUnmounted(() => {
  if (unreadTimer !== undefined) window.clearInterval(unreadTimer)
})
</script>

<template>
  <div class="workspace-view d-flex fill-height">
    <TopicSidebar
      :width="railWidth"
      :projects="projects"
      :selected-project-id="selectedProjectId"
      @update:width="onRailWidth"
      :topics="topics"
      :selected-topic-id="selectedTopicId"
      :loading-topics="loadingTopics"
      :private-active="mode === 'private'"
      :active-docs="mode === 'docs' ? docKind : null"
      :unread-map="unreadMap"
      @select-project="selectProject"
      @select-topic="selectTopic"
      @select-private="selectPrivate"
      @select-docs="selectDocs"
      @archive-topic="handleArchiveTopic"
      @unarchive-topic="handleUnarchiveTopic"
      @create-project="handleCreateProject"
      @create-topic="handleCreateTopic"
      @split-topic="handleSplitTopic"
    />

    <!-- 私聊 (飞书私聊): a normal 1:1 chat with 芝士, full width, no doc / no PR
         header / no accept box. ChatPanel hosts its own composer; @芝士 ON. -->
    <div
      v-if="mode === 'private'"
      class="workspace d-flex flex-column flex-grow-1"
      style="min-width: 0"
    >
      <div
        v-if="privateLoading"
        class="flex-grow-1 d-flex align-center justify-center"
      >
        <v-progress-circular indeterminate color="primary" />
      </div>
      <v-alert
        v-else-if="privateError"
        type="error"
        density="comfortable"
        class="ma-3"
      >
        {{ privateError }}
      </v-alert>
      <ChatPanel
        v-else
        class="flex-grow-1"
        style="min-height: 0"
        :topic="privateTopic"
        :pr-header="false"
        :default-summon="true"
        :members="projectMembers"
        :topic-list="topics"
        :show-composer="true"
        @turn-done="handleTurnDone"
        @tool-used="handleToolUsed"
        @state-changed="handleStateChanged"
        @mention-click="handleMentionClick"
        @open-file="(p: string) => docRef?.openFile?.(p)"
        @open-resource="handleOpenResource"
        @upgrade-message="handleUpgradeMessage"
        @open-topic="selectTopic"
      />
    </div>

    <!-- 项目文档 (章程/决策记录/周报集) in the main area, keeping the rail. -->
    <ProjectDocsView
      v-else-if="mode === 'docs' && selectedProjectId"
      class="flex-grow-1"
      style="min-width: 0"
      :project-id="selectedProjectId"
      :kind="docKind"
      embedded
    />

    <!-- Work topic: panes (chat | doc) above a spanning composer. -->
    <div
      v-else
      class="workspace d-flex flex-column flex-grow-1"
      style="min-width: 0"
    >
      <div class="panes d-flex flex-grow-1" style="min-width: 0; min-height: 0">
        <ChatPanel
          ref="chatRef"
          v-show="!focusMode"
          class="col col-chat"
          :style="{ flex: `0 0 ${chatPct}%` }"
          :topic="selectedTopic"
          :pr-header="!!selectedTopic && selectedTopic.kind !== 'root'"
          :members="projectMembers"
          :topic-list="topics"
          @turn-done="handleTurnDone"
          @tool-used="handleToolUsed"
          @state-changed="handleStateChanged"
          @mention-click="handleMentionClick"
          @open-file="(p: string) => docRef?.openFile?.(p)"
          @open-resource="handleOpenResource"
          @upgrade-message="handleUpgradeMessage"
          @open-topic="selectTopic"
        >
          <!-- 成果待采纳框，放在对话时间线末尾 (GitHub PR 的合并框样式) -->
          <template
            v-if="
              selectedTopic &&
              (pendingCard ||
                (selectedTopic.status === 'archived' && acceptedCard))
            "
            #timeline-end
          >
            <v-card v-if="pendingCard" variant="outlined" class="merge-box mt-2">
              <div class="merge-box__bar" />
              <div class="pa-3">
                <div class="d-flex align-center ga-2 mb-1">
                  <v-icon
                    :color="pendingCard.status === 'conflict' ? 'warning' : 'success'"
                    size="19"
                  >
                    {{
                      pendingCard.status === 'conflict'
                        ? 'mdi-source-merge'
                        : 'mdi-source-merge'
                    }}
                  </v-icon>
                  <span class="t-title">
                    {{
                      pendingCard.status === 'conflict'
                        ? '合并冲突 · 芝士处理中'
                        : '成果待采纳'
                    }}
                  </span>
                </div>
                <div
                  v-if="pendingCard.status === 'conflict'"
                  class="text-caption text-medium-emphasis mb-2"
                >
                  {{ pendingCard.note || '采纳时合并冲突，芝士正在工作区里解决。' }}
                  它在对话里汇报解决完之后，点下面重试。
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
                        改
                      </v-btn>
                    </template>
                    <v-list density="compact">
                      <v-list-subheader>改派验收人</v-list-subheader>
                      <v-list-item
                        v-for="mbr in projectMembers"
                        :key="mbr.user_handle"
                        :active="mbr.user_handle === pendingCard.reviewer_handle"
                        @click="onReassignCard(mbr.user_handle)"
                      >
                        <v-list-item-title class="text-body-2">
                          @{{ mbr.user_handle }}
                        </v-list-item-title>
                        <v-list-item-subtitle class="text-caption">
                          {{ mbr.role }}
                        </v-list-item-subtitle>
                      </v-list-item>
                      <v-list-item v-if="projectMembers.length === 0">
                        <v-list-item-title class="text-caption text-medium-emphasis">
                          暂无可选成员
                        </v-list-item-title>
                      </v-list-item>
                    </v-list>
                  </v-menu>
                </div>
                <div
                  v-if="pendingCard.routing_reason"
                  class="text-caption text-medium-emphasis mb-3"
                >
                  推荐理由：{{ pendingCard.routing_reason }}
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
                    {{
                      pendingCard.status === 'conflict' ? '重试采纳' : '采纳并归档'
                    }}
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
                  <v-btn
                    variant="outlined"
                    class="btn-secondary"
                    :loading="acceptBusy"
                    @click="onRejectCard"
                  >
                    确认退回
                  </v-btn>
                </div>
              </div>
            </v-card>

            <!-- Archived (accepted) topic: 采纳可撤销 (spec §6.3). -->
            <v-card
              v-else-if="acceptedCard"
              variant="outlined"
              class="merge-box mt-2"
            >
              <div class="merge-box__bar" />
              <div class="pa-3">
                <div class="d-flex align-center ga-2 mb-1">
                  <v-icon color="success" size="19">mdi-check-circle-outline</v-icon>
                  <span class="t-title">已采纳并归档</span>
                </div>
                <div class="text-body-2 c-muted mb-3">
                  由 <strong>@{{ acceptedCard.decided_by }}</strong> 采纳。采纳可撤销。
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
          v-if="selectedTopic && !focusMode"
          class="pane-resizer"
          title="拖动调整宽度（双击复位）"
          @mousedown.prevent="startPaneDrag"
          @dblclick="chatPct = 50"
        />
        <DocPanel
          ref="docRef"
          v-if="selectedTopic"
          class="col col-doc"
          :style="{ flex: '1 1 0', minWidth: 0 }"
          :topic="selectedTopic"
          :activity-tick="activityTick"
          :worklog="worklog"
          :working="working"
          :working-since="workingSince"
          :focus="focusMode"
          :topic-list="topics"
          @toggle-focus="focusMode = !focusMode"
          @open-topic="selectTopic"
          @topics-changed="refreshTopics"
          @comment-intent="onCommentIntent"
        />
      </div>

      <!-- 输入栏 spans 对话 + 文档, not the rail (spec §7.1) -->
      <template v-if="selectedTopic">
        <v-divider />
        <div class="composer pa-2 px-3">
          <div class="d-flex align-center ga-2 mb-1">
            <span class="chip-neutral">
              <v-icon size="12">mdi-pound</v-icon>本话题
            </span>
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
              title="@芝士 — 让芝士回复（默认不 @）"
              @click="summon = !summon"
            >
              <v-icon v-if="summon" size="13">mdi-creation</v-icon>
              @芝士
            </button>
            <v-spacer />
          </div>
          <!-- 评论模式: quote chip above the input — what this send will
               comment on. ✕ / Esc exits back to normal message mode. -->
          <div v-if="commentIntent" class="comment-mode-chip">
            <v-icon size="14" class="comment-mode-chip__icon">
              mdi-comment-quote-outline
            </v-icon>
            <span class="comment-mode-chip__label">评论</span>
            <span class="comment-mode-chip__quote">{{ commentIntent.quote }}</span>
            <button
              type="button"
              class="comment-mode-chip__x"
              title="退出评论模式（Esc）"
              @click="commentIntent = null"
            >
              ✕
            </button>
          </div>
          <!-- @-autocomplete: pick a teammate / topic while typing @ -->
          <div v-if="mentionMatches.length && !commentIntent" class="mention-menu">
            <button
              v-for="(mm, i) in mentionMatches"
              :key="mm.kind + mm.label"
              type="button"
              class="mention-menu-item"
              @click="pickMention(mm.label)"
            >
              <span
                v-if="mm.kind === 'member'"
                class="mention-avatar"
                :class="{ 'mention-avatar--agent': mm.agent }"
              >{{ mm.label.slice(0, 1).toUpperCase() }}</span>
              <span v-else class="mention-avatar mention-avatar--topic">
                <v-icon size="13">mdi-pound</v-icon>
              </span>
              <span class="mention-menu-name">{{ mm.label }}</span>
              <span v-if="mm.agent" class="mention-agent-badge">Agent</span>
              <span class="mention-menu-sub">{{ mm.sub }}</span>
              <span v-if="i === 0" class="mention-menu-hint">Enter</span>
            </button>
          </div>
          <!-- 图片输入: images waiting to go with the next send. -->
          <div v-if="pendingAtts.length || attsUploading" class="att-strip">
            <div v-for="(a, i) in pendingAtts" :key="a.path" class="att-thumb">
              <img
                :src="attachmentRawUrl(selectedTopic.id, a.path)"
                :alt="a.path"
              />
              <button
                type="button"
                class="att-remove"
                title="移除"
                @click="removePendingAtt(i)"
              >
                ×
              </button>
            </div>
            <v-progress-circular
              v-if="attsUploading"
              indeterminate
              size="18"
              width="2"
            />
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
              :placeholder="
                commentIntent
                  ? '写评论…（Enter 发送，Esc 退出评论模式）'
                  : summon
                    ? '让芝士做点什么…（Enter 发送，Shift+Enter 换行，可粘贴图片）'
                    : '发条消息…（默认不 @ 芝士；点 @芝士 让它回复）'
              "
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
              :disabled="
                commentIntent
                  ? !draft.trim()
                  : !composerReady || (!draft.trim() && !pendingAtts.length)
              "
              @click="sendDraft"
            />
          </div>
        </div>
      </template>
    </div>

    <v-snackbar
      v-model="hasError"
      color="error"
      timeout="4000"
      location="bottom"
    >
      {{ globalError }}
    </v-snackbar>
  </div>
</template>

<style scoped>
.workspace-view {
  width: 100%;
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
  position: absolute;
  top: -6px;
  right: -6px;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  border: 1px solid var(--line);
  background: var(--surface);
  color: var(--muted);
  font-size: 13px;
  line-height: 1;
  cursor: pointer;
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
  border-radius: 0 8px 8px 0;
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
  flex: 0 0 auto;
  border: none;
  background: transparent;
  color: var(--faint);
  font-size: 12px;
  line-height: 1;
  padding: 2px 4px;
  border-radius: 4px;
  cursor: pointer;
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
  border: 1px solid var(--border, #e0e0e0);
  border-radius: 8px;
  overflow: hidden;
  background: var(--surface);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.08);
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
  background: var(--fill, #f5f5f5);
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
  color: #fff;
  background: #8a94a3;
  flex: none;
}
.mention-avatar--agent {
  background: var(--accent, #f57f17);
}
.mention-avatar--topic {
  background: var(--fill, #f0f1f3);
  color: var(--muted, #6b6b6b);
}
.mention-menu-name {
  font-weight: 500;
}
.mention-agent-badge {
  font-size: 0.65rem;
  font-weight: 600;
  padding: 0 5px;
  border-radius: 4px;
  color: rgb(var(--v-theme-primary));
  background: rgba(var(--v-theme-primary), 0.12);
}
.mention-menu-sub {
  font-size: 0.75rem;
  color: var(--text-muted, #999);
}
.mention-menu-hint {
  margin-left: auto;
  font-size: 0.7rem;
  color: var(--text-muted, #aaa);
}
.composer-input :deep(textarea) {
  font-size: 14px;
  line-height: 1.5;
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
  color: #fff;
}

@media (max-width: 960px) {
  .panes {
    flex-direction: column;
  }
}
</style>
