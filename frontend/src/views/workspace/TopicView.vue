<script setup lang="ts">
import type { AgentControlState, Block, ChatAttachment, Topic, TopicMemberRow } from '@/cx_types'
import type { DocReviewRequest } from '@/lib/docReview'
import type { MemberActivityLine } from '@/lib/memberActivity'
import type { CardPhase } from '@/lib/topicState'
import type { PreviewLocate, SubmitPreviewQuestion } from '../../lib/previewQuestion'

import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'

import { useEscapeLayer } from '@/composables/useEscapeStack'
import { usePageTitle } from '@/composables/usePageTitle'
import { useRoomTabHistory } from '@/composables/useRoomTabHistory'
import { useTopicMemory } from '@/composables/useTopicMemory'
import { useCompactDesktop } from '@/composables/useWorkspaceLayout'

import { useCommands } from '@/commands'
import { useTopBarBack } from '@/components/common/topBarBack'
import PushPermissionPrompt from '@/components/PushPermissionPrompt.vue'
import TopicHeader from '@/components/TopicHeader.vue'
import WorkPanel from '@/components/WorkPanel.vue'
import { t } from '@/i18n'
import { agentNames, memberName } from '@/lib/agentNames'
import { announceComments } from '@/lib/docCommentSignals'
import { warmRoutesWhenIdle } from '@/lib/routePrefetch'
import { cachedTopicPanel, fetchTopicMembers } from '@/lib/topicPanelCache'
import { onTopicRosterChange } from '@/lib/topicRosterChanges'
import { topicTitle } from '@/lib/topicState'
import { userRefRoute } from '@/lib/userRef'
import { myHandle } from '@/me'
import { useWorkspaceStore } from '@/stores/workspace'
import TopicChatColumn from '@/views/workspace/TopicChatColumn.vue'

// 话题视图: ONE topic header, then the chat | 工作面板 split. The input bar is
// the chat column's own — it used to span both columns from here, which read as
// addressing the whole topic while 99% of what it sent was a chat message only
// the left column shows. Which topic is open is a route param, and ProjectShell
// keys this view on it: every topic gets a fresh instance, so nothing below — or in
// any child — can carry one topic's state into the next.
defineOptions({ name: 'TopicView' })

const props = defineProps<{ projectId: string; topicId: string }>()
const { mdAndUp } = useDisplay()
// 平板横放那一档（960–1180）：对话占满整宽，工作面板是从右边拉进来的浮层。
const compact = useCompactDesktop()
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
// 页签和浏览器历史怎么对应（桌面 replace；手机上 Back 先回到对话）见 useRoomTabHistory。
const phone = computed(() => !mdAndUp.value)
const tabHistory = useRoomTabHistory(phone)
function onPanelTab(key: string) {
  tabHistory.goTab(key)
}
// 手机上不在对话那一格时，顶栏的 ← 和 Back 一样先回到对话。
useTopBarBack(() =>
  phone.value && !tabHistory.onChat.value ? { label: t('work.room.backToChat'), onBack: tabHistory.toChat } : null
)
void tabHistory.ensureChatBehind()

// 看板上点开的那张卡。**一件活不是地点**：做它的分身住在这个房间的会话里，所以
// 打开一张卡不离开房间，只是总览那一格往下钻一层——地址里记的就是这一层，于是
// 「你看一下这条活」是一条能发出去的链接。
const openCardId = computed(() => {
  const q = route.query.card
  return typeof q === 'string' && q ? q : null
})
// 「去验收」：决策在聊天，审查在面板 —— 它不把人带去任何地方，只把右栏切到
// 「改动」那一格。对话栏末尾那张验收卡和总览里那张卡上的按钮是同一个动作，所以
// 只有这一处定义（`chatEvents.review` 和 `<WorkPanel @review>` 都指过来）。
function onReview() {
  onPanelTab('changes')
}

// 地址点名的一条消息（搜索结果、别人发来的链接）：对话栏打开时停在它上面。
const focusBlock = computed(() => {
  const q = route.query.block
  return typeof q === 'string' && q ? q : null
})
// 同时开着一张卡时，点名的是卡里的那一条（卡里的对话不在房间的对话里）。
const chatFocusBlock = computed(() => (openCardId.value ? null : focusBlock.value))
const cardFocusBlock = computed(() => (openCardId.value ? focusBlock.value : null))

function onOpenCard(taskId: string | null) {
  if (openCardId.value === taskId) return
  // 桌面上是 push：往下钻一层是「去了一个地方」，浏览器的返回该退回看板。
  tabHistory.openCard(taskId)
}

// ---- 平板横放：工作面板的收 / 开 ----
// 这一档里对话占满整宽，面板是一只从右边拉进来的浮层，默认收起。「面板开着」这件事
// 就写在地址里——`?tab=` 或 `?card=` 就是「有人打开了这一格」，于是对话里点「查看改
// 动」、点开一张卡、别人发来的链接，全走同一条路（`onPanelTab` / `onOpenCard` 本来就
// 在改地址）。宽档里面板一直开着（就在对话旁边），手机上是 tab 栏的第一格，两处都不
// 经过这里。
const panelOpen = computed(() => !compact.value || !!panelTab.value || !!openCardId.value)
// 收起之后从页头那颗开关再打开时回到哪一格：面板此刻在画哪一格。这一格未必来自地址
// ——平板横放里进房间时自动选中的那一格（芝士在干活就是「现场」、卡等你验收就是「改
// 动」）只留在面板里、没写进地址，收起再打开要回到它。量不到就落在总览。
function openPanel() {
  const want = panelRef.value?.activeTab() ?? panelTab.value ?? 'overview'
  void router.replace({ query: { ...route.query, tab: want } })
}
function closePanel() {
  if (!compact.value) return
  // 清掉地址里的 tab / card：面板收起了，地址就不该再写着一格开着——不然下一次点
  // 「查看改动」时 goTab 会因为「已经在 changes」而什么都不做，面板打不开。
  void router.replace({ query: { ...route.query, tab: undefined, card: undefined } })
  // Esc 关掉浮层，焦点回到打开它那颗开关（键盘和读屏用户必须回得去）。
  void nextTick(() => document.querySelector<HTMLElement>('[data-panel-toggle]')?.focus())
}
function togglePanel() {
  if (panelOpen.value) closePanel()
  else openPanel()
}
// 平板横放：面板浮层按 Esc 收起，焦点回到页头那颗开关。这一档里二级侧栏浮层也可能
// 同时开着，两层共用一个 Esc 栈：一下 Esc 只关最上面那层（后打开的那层），第二下才
// 轮到另一层。见 useEscapeStack。
useEscapeLayer(
  computed(() => compact.value && panelOpen.value),
  closePanel
)

const AUTHOR = myHandle()

// URL 里的这个 id 指向一个房间。列表里没有就直接去问它——深链接、刷新，都走这条路。
const selectedTopic = computed<Topic | null>(() => store.placeById(props.topicId))

// 手机顶栏写的是当前页的标题，而这一页的标题是话题名——路由上没有，只有打开了
// 才知道。桌面顶栏不显示它，但浏览器标签页同样受益。
const { setDynamicTitle, clearDynamicTitle } = usePageTitle()
watch(
  selectedTopic,
  (topic) => {
    if (topic) setDynamicTitle(topicTitle(topic), 'workspace-topic')
    else clearDynamicTitle('workspace-topic')
  },
  { immediate: true }
)
onUnmounted(() => clearDynamicTitle('workspace-topic'))

// 话题画出来之后，趁浏览器空着把从这里最常去的几页的代码先下下来：看板、资料库、
// 项目文档、搜索。点过去时就只剩取数据那一段等待（lib/routePrefetch.ts）。
let cancelRouteWarm: (() => void) | null = null
onMounted(() => {
  const params = { projectId: props.projectId }
  cancelRouteWarm = warmRoutesWhenIdle(router, [
    { name: 'workspace-running', params },
    { name: 'project-library', params },
    { name: 'project-docs', params: { ...params, kind: 'charter' } },
    { name: 'project-search', params },
  ])
})
onUnmounted(() => cancelRouteWarm?.())
// The list is still on its way, so "not found" is not yet a fact. Neither is it
// one while this id is being asked about directly — the path a deep link takes.
const resolving = computed(
  () =>
    !selectedTopic.value && (store.loadingTopics || store.topics.length === 0 || store.isResolvingPlace(props.topicId))
)

function openTopic(topicId: string) {
  if (topicId === props.topicId) return
  void router.push({ name: 'workspace-topic', params: { projectId: props.projectId, topicId } })
}

// ---- Layout: the chat|panel split, persisted across sessions (in the store, so
// the sidebar's own width sits in the same record). This splitter is the ONLY
// width control in the workspace now — the tool drawer used to carry a second
// one of its own (`cheesex.toolWidth`), plus a 钉住 toggle that decided whether
// the doc made room for it at all.
const { focusMode } = useTopicMemory() // 专注模式 (spec §7.1): session-only, a transient mode
// 平板横放那一档里对话永远占满整宽，没有「让开一半」这回事，专注模式在这一档里不
// 成立：一进来就把它关掉，免得从宽档带来的那个开关和这里的布局打架。
watch(
  compact,
  (on) => {
    if (on) focusMode.value = false
  },
  { immediate: true }
)
// 专注模式只在宽档的桌面上有：手机上一栏，平板横放里对话本来就是整宽。
useCommands(() =>
  mdAndUp.value && !compact.value
    ? [
        {
          id: 'room.focus',
          title: focusMode.value ? t('work.room.menu.exitFocus') : t('work.room.menu.focus'),
          icon: focusMode.value ? 'mdi-arrow-collapse' : 'mdi-arrow-expand',
          run: () => (focusMode.value = !focusMode.value),
        },
      ]
    : []
)
// 对话那一栏的宽度：宽档是 `0 0 N%`（可拖的分隔），平板横放里它吃掉整宽——面板浮在
// 上面，不再分地方。
const chatStyle = computed(() => (compact.value ? { flex: '1 1 0', minWidth: 0 } : { flex: `0 0 ${store.chatPct}%` }))
// 收起 / 拉开的那一下里，栏在变窄变宽，里面的东西不跟着变：几百条消息每一帧按新
// 宽度重新折行，既费又难看。把里面钉在这一栏落定时的宽度上，栏只是把它裁开、露出。
function freezeChatWidth(el: Element) {
  const panes = (el as HTMLElement).parentElement
  if (!panes) return
  ;(el as HTMLElement).style.setProperty('--chat-frozen-w', `${(panes.clientWidth * store.chatPct) / 100}px`)
}
const panelRef = ref<{
  pulse: () => void
  highlightTurn: (turnId: string) => void
  openFile?: (path: string, taskId?: string | null) => void
  siteBlock?: (block: Block) => void
  reviewDoc?: (request: DocReviewRequest) => void
  previewShown?: () => void
  // 面板此刻在画哪一格。收起再打开要回到它——自动选中的那一格不在地址里，只能问它。
  activeTab: () => string
} | null>(null)
const chatColumn = ref<{
  connected: boolean
  reloadAccept: (silent?: boolean) => void
  reloadFeedback: () => void
  reloadSkills: () => void
  say: (content: string, attachments?: ChatAttachment[]) => boolean
  submitQuestion: SubmitPreviewQuestion
} | null>(null)

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

// The chat column's own composer is the one this topic uses; TopicView only
// needs a handle on the panel it lives in for the connection dot in the header.
const composerReady = computed(() => !!chatColumn.value?.connected)

// 预览里指出的一处位置，作为一条普通消息进这个房间的对话。没有新接口，也没有
// 长期锚点：它只在下一轮被读一次。
function onLocate(payload: PreviewLocate) {
  chatColumn.value?.say(payload.message, payload.attachments)
}
const submitQuestion: SubmitPreviewQuestion = (request) => chatColumn.value?.submitQuestion(request) ?? false

// 对话那一栏在两端挂在不同位置（左栏 / tab 栏第一格），但接的是同一组事件。
const chatEvents = {
  'turn-done': handleTurnDone,
  working: handleWorking,
  activity: (lines: MemberActivityLine[]) => (activity.value = lines),
  'agent-control': (state: AgentControlState) => (agentControl.value = state),
  'site-block': (block: Block) => panelRef.value?.siteBlock?.(block),
  'site-turns': (turns: Record<string, number>) => (siteTurns.value = turns),
  'state-changed': handleStateChanged,
  // 芝士摆出来一份东西：面板立刻看一眼当前预览，不等轮询。
  'preview-shown': () => panelRef.value?.previewShown?.(),

  'mention-click': handleMentionClick,
  'open-file': (path: string, taskId?: string | null) => panelRef.value?.openFile?.(path, taskId),
  'open-resource': handleOpenResource,
  'upgrade-message': handleUpgradeMessage,
  'open-topic': openTopic,
  'open-card': onOpenCard,
  phase: (p: CardPhase) => (cardPhase.value = p),
  review: onReview,
}

// 有没有队友正在这个话题里跑一轮 —— 工作面板的「现场」那一格和推送提示读它。
const working = ref(false)

// 页头那颗点说的是「这个房间跟不跟得上」——它和工作条必须同源。对话栏报上来的
// `composerReady` 是 socket 的那一帧，而 socket 会在连接打嗝时闪断：那一瞬它说
// 未连接，可这一轮还在跑（工作条写着「正在工作 · 重试中」，因为重试就是靠它自己
// 接着干）。一轮没跑完，这个房间就是连着的 —— 断了它没法把这一轮干完。所以两个
// 一起看：只要工作条在说「正在工作」，页头就不能同时说「未连接」。
const roomConnected = computed(() => composerReady.value || working.value)
// 此刻谁在这个房间里忙，对话栏从 socket 上学来：现场那一格画其中在干活的队友。
const activity = ref<MemberActivityLine[]>([])
// 会话状态的最近一帧，对话栏从 socket 上收到，现场那格的会话详情读它。
const agentControl = ref<AgentControlState | null>(null)
// 正在跑的轮次各自从什么时候开始，对话栏从 socket 上算出来，现场读它分出哪一组还在进行。
const siteTurns = ref<Record<string, number>>({})

// ---- 采纳卡处在哪一段 ----
// The accept card owns its own data, but the panel opens on the tab its stage
// calls for, so the one word travels up rather than the card list travelling
// out. undefined until the card box has actually answered — the panel does not
// get to pick a tab on an answer nobody has yet. The room header does not show
// it: a card's stage is the card's, shown on the card and on the board.
const cardPhase = ref<CardPhase | undefined>(undefined)

// 芝士 开工 / 收工，由对话栏按轮次生命周期报上来。这是 `working` 唯一的开关：
// 「现场」那一格的存在与否读它，所以它必须在开工那一刻就翻过来——而不是等到它第
// 一次动手。一条 @芝士 开出来的 agent 可能先想上半分钟才动手，那半分钟里右边什
// 么都没有，除非刷新一次页面。
function handleWorking(now: boolean) {
  working.value = now
}

function handleTurnDone() {
  // 这一轮干完了 —— 现场那条时间线就是它留下的记录。
  working.value = false
  activityTick.value += 1
  void store.refreshTopics()
  // 芝士's reply landed after our read cursor — the user is watching this
  // topic, so re-bump the cursor before refreshing badges (other topics that
  // got messages in the background DO light up).
  store.markRead(props.topicId)
  void store.refreshUnread()
}

// A platform resource in this room changed (the API handler that changed it
// sent the frame) — refresh the affected panel live (§3.1.1).
function handleStateChanged(resource: string) {
  if (resource === 'topics') void store.refreshTopics()
  // 文档的评论变了：这个房间的文档那一格自己重读评论，不重读整篇。
  else if (resource === 'comments') announceComments(props.topicId, { kind: 'changed' })
  // silent：卡是这一刻递上来的，框里原有的留在屏幕上换新，不先清空再长出来。
  else if (resource === 'accept') chatColumn.value?.reloadAccept(true)
  // 提案卡落下、被发出去、被「不用」：卡片跟着变，不等刷新。
  else if (resource === 'feedback') chatColumn.value?.reloadFeedback()
  // 技能的提议落下、被保存或被拒：那张卡跟着变。
  else if (resource === 'skills') chatColumn.value?.reloadSkills()
  else activityTick.value += 1 // doc / notify → reload
}

// An action card's button → open the relevant view (§3.1.1 控件).
async function handleOpenResource(resource: string, turnId?: string, review?: DocReviewRequest) {
  if (resource === 'site') {
    // 对话里在动的那个头像：它此刻在干什么，去现场看。
    focusMode.value = false
    onPanelTab('site')
  } else if (resource === 'changes') {
    // 本轮摘要的「查看改动」: the diff is a tab away, not a new page.
    focusMode.value = false
    onPanelTab('changes')
  } else if (resource === 'accept') {
    chatColumn.value?.reloadAccept(true)
  } else if (resource === 'doc') {
    // B1 Phase 2: highlight the exact paragraphs this turn changed (falls back to
    // a whole-doc pulse when the turn's blocks aren't tagged). Leaving focus mode
    // re-renders the editor, which recreates its DOM — wait for that render to
    // settle before highlightTurn tags + flashes, or the flash is wiped instantly.
    focusMode.value = false
    await nextTick()
    // 「查看改动」：在正文里一处处标出这个人让 AI 队友改的那几处。
    if (review) panelRef.value?.reviewDoc?.(review)
    else if (turnId) panelRef.value?.highlightTurn(turnId)
    else panelRef.value?.pulse()
  }
  // topics: the topic panel is already in view next to the chat.
}

// A clicked <@handle> mention chip → open that teammate's member page.
function handleMentionClick(handle: string) {
  void router.push(userRefRoute(handle, props.projectId))
}

// ⤴ 升级 from a message bubble (eval A1). 房间里的消息变成这个房间的一张卡，
// 私聊里的变成一个新房间——两种落点，两种去处。
async function handleUpgradeMessage(messageId: string) {
  const upgraded = await store.upgradeMessage(messageId)
  if (!upgraded) return
  if (upgraded.kind === 'card') onOpenCard(upgraded.id)
  else openTopic(upgraded.id)
}

// 「新消息从哪开始」只有开话题的那一瞬间知道：markRead 一跑，未读数就归零了。
// 所以在归零之前抓一次，交给对话栏去画那条线。
const unreadOnOpen = store.unreadMap[props.topicId] ?? 0

// 这个房间名册上每个 handle 叫什么。「现场」那一格给每一行署名用它，人和 AI 队
// 友一个规矩：署作者，不署「这个房间的那位」——一个房间可以先后交给两个队友。
// 那一格自己不拉名册，所以在这里拉一次传下去。AI 队友的名字和对话栏同一个出处
// （`agentNames`）：已经不在这间房里的队友，项目名册上还叫得出。
const roomMembers = ref<TopicMemberRow[]>(cachedTopicPanel('members', props.topicId)?.data ?? [])
const memberNames = computed<Record<string, string>>(() => ({
  ...Object.fromEntries(roomMembers.value.map((m) => [m.member_handle, memberName(m) || m.member_handle])),
  ...Object.fromEntries(agentNames(roomMembers.value, store.members)),
}))
async function loadMemberNames() {
  try {
    roomMembers.value = (await fetchTopicMembers(props.topicId)).data
  } catch {
    // 名册拉不到，现场那一格就按 handle 署名——比空白好，也比报错好。
  }
}
void loadMemberNames()
// 名册抽屉里加了人、移了人，这份跟着重拉：刚请进来的队友在「现场」那一格也要叫得出名字。
onUnmounted(
  onTopicRosterChange((topicId) => {
    if (topicId === props.topicId) void loadMemberNames()
  })
)

// 这个 id 在侧栏那张表里找不到的话，直接问它——支线走的永远是这条路。
// 先等它答完再记已读：已读位只有房间有，不知道这是房间还是支线就记，
// 等于对每一条支线都白打一次会 404 的请求。
async function openPlace() {
  await store.loadPlace(props.topicId)
  store.markRead(props.topicId)
}
void openPlace()
</script>

<template>
  <div class="topic-view d-flex flex-column fill-height" style="min-width: 0">
    <div v-if="!selectedTopic" class="flex-grow-1 d-flex align-center justify-center">
      <v-progress-circular v-if="resolving" indeterminate color="primary" />
      <div v-else class="text-center">
        <div class="t-body c-muted">{{ t('work.topic.notFound') }}</div>
        <div class="t-meta mt-1">{{ t('work.topic.notFoundHint') }}</div>
      </div>
    </div>

    <template v-else>
      <!-- Screen-reader heading for the room. Text from `topicTitle`, the same
           source as the `workspace-topic` dynamic title the top bar reads. The
           room name is drawn as a span in TopicHeader (not a heading); the
           project shell adds its own h1 for the project. Hidden: the name is
           already on screen. -->
      <h1 class="visually-hidden">{{ topicTitle(selectedTopic) }}</h1>
      <!-- 一条话题头部，横跨对话和工作面板 -->
      <TopicHeader
        :topic="selectedTopic"
        :members="store.members"
        :me="AUTHOR"
        :connected="roomConnected"
        :focus="focusMode"
        :panel-open="panelOpen"
        @toggle-focus="focusMode = !focusMode"
        @toggle-panel="togglePanel"
        @open-topic="openTopic"
        @rename="(title) => store.renameTopic(topicId, title)"
      />

      <!-- 「本轮运行时间可能较长，完成后通知你」——问推送权限的那一刻。它自己决定
         什么时候出现（这一轮跑过一分钟、而且这个浏览器还没问过），平常什么都不
         画。放在这里而不是首屏：见组件自己的说明。 -->
      <PushPermissionPrompt :working="working" />

      <div class="panes d-flex flex-grow-1" style="min-width: 0; min-height: 0; position: relative">
        <!-- 桌面：对话是左边那一栏，和工作面板之间有一条可拖的分隔。
           专注模式开关时这一栏像抽屉一样收起 / 拉开，而不是一下消失、面板一下跳宽：
           人要看得出面板是从哪儿长过来的。平板横放那一档里这一栏占满整宽，面板是浮在
           它上面的浮层（见下面的 panel-host--sheet），不再分地方。 -->
        <Transition name="focus-chat" @before-enter="freezeChatWidth" @before-leave="freezeChatWidth">
          <TopicChatColumn
            v-if="mdAndUp"
            v-show="!focusMode"
            ref="chatColumn"
            class="col col-chat"
            :style="chatStyle"
            :topic="selectedTopic"
            :members="store.members"
            :topic-list="store.topics"
            :unread-on-open="unreadOnOpen"
            :focus-block="chatFocusBlock"
            v-on="chatEvents"
          />
        </Transition>
        <div
          v-if="mdAndUp && !focusMode && !compact"
          class="pane-resizer"
          :title="t('work.topic.resize')"
          @mousedown.prevent="startPaneDrag"
          @dblclick="store.setChatPct(50)"
        />

        <!-- 平板横放：面板浮层背后的遮罩。点它收起面板——和 Esc 同一条路。 -->
        <Transition name="panel-scrim">
          <div v-if="compact && panelOpen" class="panel-scrim" @click="closePanel" />
        </Transition>

        <!-- 工作面板。宽档里它是对分里右边那一栏（今天的样子，行内排布）；
             平板横放里它是一只从右边拉进来的浮层：对话占满整宽，面板默认收起，
             打开它的是页头那颗开关（或地址里的 ?tab= / ?card=）。收起时 visibility
             一并藏掉，浮层里的东西不进 tab 序、也不进读屏的树。 -->
        <div
          :id="compact ? 'topic-panel' : undefined"
          class="col col-doc panel-host"
          :class="{ 'panel-host--sheet': compact, 'panel-host--open': compact && panelOpen }"
          :style="compact ? undefined : { flex: '1 1 0', minWidth: 0 }"
        >
          <WorkPanel
            ref="panelRef"
            :submit-question="submitQuestion"
            :agent-name="store.agentName"
            :agent-handle="store.agentHandle"
            :members="store.members"
            :activity="activity"
            :topic="selectedTopic"
            :activity-tick="activityTick"
            :working="working"
            :agent-control="agentControl"
            :site-turns="siteTurns"
            :topic-list="store.topics"
            :tab="panelTab"
            :card-phase="cardPhase"
            :with-chat="!mdAndUp"
            :compact="compact"
            :open-card-id="openCardId"
            :card-focus-block="cardFocusBlock"
            :member-names="memberNames"
            @open-topic="openTopic"
            @open-card="onOpenCard"
            @review="onReview"
            @mention-click="handleMentionClick"
            @update:tab="onPanelTab"
            @locate="onLocate"
          >
            <!-- 手机：一屏放不下两栏，对话是 tab 栏里的第一格。 -->
            <template #chat>
              <TopicChatColumn
                ref="chatColumn"
                class="col col-chat flex-grow-1"
                :topic="selectedTopic"
                :members="store.members"
                :topic-list="store.topics"
                :unread-on-open="unreadOnOpen"
                :focus-block="chatFocusBlock"
                v-on="chatEvents"
              />
            </template>
          </WorkPanel>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
/* 这条不参与伸缩：它有内容时占自己那点高度，没内容时整个不在 DOM 里，四格的高度
   都不会因为它变来变去。 */
.topic-view {
  flex: 1 1 auto;
  overflow: hidden;
  /* 底色分层：侧栏坐在 background (--canvas) 上，内容区是它上面那张 surface。
     这条视图以前不画底、直接透出 body 的 --canvas，于是侧栏和正文同色，两者
     之间只剩一条边线在撑。 */
  background: var(--surface);
  /* Switching topics on a wide screen cross-fades this view only (lib/viewTransition.ts). */
  view-transition-name: topic-view;
}
.col {
  min-width: 0;
}
/* 专注模式：对话栏是一只侧抽屉（§9：整块进出 --dur-slow，走掉快一档）。动的是
   flex-basis；`!important` 是为了压过模板上那条内联的 `flex: 0 0 N%`。 */
.focus-chat-enter-active,
.focus-chat-leave-active {
  overflow: hidden;
}
.focus-chat-enter-active {
  transition:
    flex-basis var(--dur-slow) var(--ease-out),
    opacity var(--dur-slow) var(--ease-out);
}
.focus-chat-leave-active {
  transition:
    flex-basis var(--dur-base) var(--ease-in),
    opacity var(--dur-base) var(--ease-in);
}
.focus-chat-enter-from,
.focus-chat-leave-to {
  flex-basis: 0% !important;
  opacity: 0;
}
.focus-chat-enter-active > :deep(*),
.focus-chat-leave-active > :deep(*) {
  width: var(--chat-frozen-w);
  min-width: var(--chat-frozen-w);
}
/* 对话和面板之间那条可拖的线。看得见的只有 1px，和页面上别的分隔线一样重；能抓
   的范围左右各多 4px（::before），不然一条细线很难按准。它原来是一条 5px 的灰带，
   比屏幕上任何一条线都粗，悬停还变琥珀——琥珀留给主操作。 */
.pane-resizer {
  position: relative;
  z-index: var(--z-raised);
  flex: 0 0 1px;
  cursor: col-resize;
  background: var(--line);
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.pane-resizer::before {
  content: '';
  position: absolute;
  inset: 0 -4px;
}
.pane-resizer:hover {
  background: var(--faint);
}

/* 工作面板的外壳（里面就是 WorkPanel 本身）。宽档里它是一个普通的 flex 子项——
   今天的样子；平板横放里它被下面的 --sheet 改成一只浮层。 */
.panel-host {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-width: 0;
  min-height: 0;
}

/* 平板横放的面板浮层：从对话右边拉进来的一张纸。收起时同时走进屏幕右侧、并把
   visibility 藏掉——藏掉的浮层不占 tab 序，也不进读屏的树，这比只 translate 出去
   干净。visibility 的过渡带一个等于位移时长的延迟：拉开时立刻可见，收起时等位移
   走完才藏。 */
.panel-host--sheet {
  position: absolute;
  top: 0;
  right: 0;
  bottom: 0;
  z-index: var(--z-panel-2);
  width: min(520px, 100%);
  background: var(--surface);
  border-left: 1px solid var(--line);
  box-shadow: var(--shadow-2);
  transform: translateX(100%);
  visibility: hidden;
  transition:
    transform var(--dur-base) var(--ease-standard),
    visibility 0s linear var(--dur-base);
}
.panel-host--open {
  transform: none;
  visibility: visible;
  transition:
    transform var(--dur-base) var(--ease-out),
    visibility 0s;
}

/* 浮层背后的遮罩：点它就和按 Esc 一样收起面板。 */
.panel-scrim {
  position: absolute;
  inset: 0;
  z-index: var(--z-panel);
  background: var(--overlay);
}
.panel-scrim-enter-active,
.panel-scrim-leave-active {
  transition: opacity var(--dur-base) var(--ease-standard);
}
.panel-scrim-enter-from,
.panel-scrim-leave-to {
  opacity: 0;
}

/* 用户关掉了动画就别拉。 */
@media (prefers-reduced-motion: reduce) {
  .panel-host--sheet,
  .panel-host--open,
  .panel-scrim-enter-active,
  .panel-scrim-leave-active {
    transition: none;
  }
}
</style>
