<script setup lang="ts">
import type { Topic } from '@/cx_types'
import type { CardPhase, TopicPhase } from '@/lib/topicState'

import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'

import { usePageTitle } from '@/composables/usePageTitle'

import { getTopicAgent } from '@/api'
import PushPermissionPrompt from '@/components/PushPermissionPrompt.vue'
import RoomEnvironmentStatus from '@/components/RoomEnvironmentStatus.vue'
import TopicHeader from '@/components/TopicHeader.vue'
import WorkPanel from '@/components/WorkPanel.vue'
import { topicPhase } from '@/lib/topicState'
import { myHandle } from '@/me'
import { useWorkspaceStore } from '@/stores/workspace'
import TopicChatColumn from '@/views/workspace/TopicChatColumn.vue'

// 话题视图: ONE topic header, then the chat | 工作面板 split. The input bar is
// the chat column's own — it used to span both columns from here, which read as
// addressing the whole topic while 99% of what it sent was a chat message only
// the left column shows. Which topic is open is a route param — this component
// is reused across topic switches, so everything topic-scoped below keys off
// `props.topicId`.
defineOptions({ name: 'TopicView' })

const props = defineProps<{ projectId: string; topicId: string }>()
const { mdAndUp } = useDisplay()
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

function onOpenCard(taskId: string | null) {
  if (openCardId.value === taskId) return
  // push，不是 replace：往下钻一层是「去了一个地方」，浏览器的返回该退回看板。
  const query = { ...route.query, tab: 'overview', card: taskId ?? undefined }
  void router.push({ query })
}

const AUTHOR = myHandle()

// URL 里的这个 id 指向一个房间。列表里没有就直接去问它——深链接、刷新，都走这条路。
const selectedTopic = computed<Topic | null>(() => store.placeById(props.topicId))

// 手机顶栏写的是当前页的标题，而这一页的标题是话题名——路由上没有，只有打开了
// 才知道。桌面顶栏不显示它，但浏览器标签页同样受益。
const { setDynamicTitle, clearDynamicTitle } = usePageTitle()
watch(
  selectedTopic,
  (topic) => {
    if (topic) setDynamicTitle(topic.title, 'workspace-topic')
    else clearDynamicTitle('workspace-topic')
  },
  { immediate: true }
)
onUnmounted(() => clearDynamicTitle('workspace-topic'))
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
const focusMode = ref(false) // 专注模式 (spec §7.1): session-only, a transient mode
const panelRef = ref<{
  pulse: () => void
  highlightTurn: (turnId: string) => void
  openFile?: (path: string, taskId?: string | null) => void
} | null>(null)
const chatColumn = ref<{
  connected: boolean
  reloadAccept: (silent?: boolean) => void
  say: (content: string) => boolean
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
function onLocate(message: string) {
  chatColumn.value?.say(message)
}

// 对话那一栏在两端挂在不同位置（左栏 / tab 栏第一格），但接的是同一组事件。
const chatEvents = {
  'turn-done': handleTurnDone,
  working: handleWorking,
  'state-changed': handleStateChanged,
  'mention-click': handleMentionClick,
  'open-file': (path: string, taskId?: string | null) => panelRef.value?.openFile?.(path, taskId),
  'open-resource': handleOpenResource,
  'upgrade-message': handleUpgradeMessage,
  'open-topic': openTopic,
  phase: (p: CardPhase) => (cardPhase.value = p),
  review: onReview,
}

// 芝士 是不是正在这个话题里干活 —— 话题头上的状态词和工作面板的 tab 都读它。
const working = ref(false)

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

// A `cheese <sub>` command changed a platform resource mid-turn (it runs as Bash,
// so we can't key off a tool name) — refresh the affected panel live (§3.1.1).
function handleStateChanged(resource: string) {
  if (resource === 'topics') void store.refreshTopics()
  else if (resource === 'accept') chatColumn.value?.reloadAccept()
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
  } else if (resource === 'changes') {
    // 本轮摘要的「查看改动」: the diff is a tab away, not a new page.
    focusMode.value = false
    onPanelTab('changes')
  } else if (resource === 'accept') {
    chatColumn.value?.reloadAccept()
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

// ⤴ 升级 from a message bubble (eval A1). 房间里的消息变成这个房间的一张卡，
// 私聊里的变成一个新房间——两种落点，两种去处。
async function handleUpgradeMessage(messageId: string) {
  const upgraded = await store.upgradeMessage(messageId)
  if (!upgraded) return
  if (upgraded.kind === 'card') onOpenCard(upgraded.id)
  else openTopic(upgraded.id)
}

// Everything topic-scoped resets when the URL names a different topic.
// 「新消息从哪开始」只有开话题的那一瞬间知道：markRead 一跑，未读数就归零了。
// 所以在归零之前抓一次，交给对话栏去画那条线。
const unreadOnOpen = ref(0)

// 这个房间现在交给的 AI 队友叫什么。「现场」那一格给它干的每一行署名，而那一格
// 自己不拉名册。一个项目可以有好几个队友，所以这个名字不能写死。
const agentName = ref('芝士')
async function loadAgentName(id: string) {
  try {
    const agent = await getTopicAgent(id)
    if (props.topicId === id) agentName.value = agent.display_name || '芝士'
  } catch {
    // 支线（和旧环境）没有这条路由。写死的兜底名字比空白好，也比报错好。
  }
}
watch(
  () => props.topicId,
  () => {
    if (props.topicId) void loadAgentName(props.topicId)
  },
  { immediate: true }
)
watch(
  () => props.topicId,
  async (id) => {
    working.value = false
    if (!id) return
    unreadOnOpen.value = store.unreadMap[id] ?? 0
    // 这个 id 在侧栏那张表里找不到的话，直接问它——支线走的永远是这条路。
    // 先等它答完再记已读：已读位只有房间有，不知道这是房间还是支线就记，
    // 等于对每一条支线都白打一次会 404 的请求。
    await store.loadPlace(id)
    store.markRead(id)
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
        @open-topic="openTopic"
      />

      <!-- 「这个房间还没准备好」——横跨四格，因为环境没起来时改动/现场/预览同样
           都是空的，人可能正在任何一格里等。在标题**之下**：一个会消失的临时状态
           不该把常驻的标题挤下去。总览（root）没有自己的运行环境，那里不显示。 -->
      <RoomEnvironmentStatus
        v-if="selectedTopic.kind !== 'root'"
        class="env-strip"
        :project-id="projectId"
        :topic-id="topicId"
      />

      <!-- 「本轮运行时间可能较长，完成后通知你」——问推送权限的那一刻。它自己决定
           什么时候出现（这一轮跑过一分钟、而且这个浏览器还没问过），平常什么都不
           画。放在这里而不是首屏：见组件自己的说明。 -->
      <PushPermissionPrompt class="env-strip" :working="working" />

      <div class="panes d-flex flex-grow-1" style="min-width: 0; min-height: 0; position: relative">
        <!-- 桌面：对话是左边那一栏，和工作面板之间有一条可拖的分隔。 -->
        <TopicChatColumn
          v-if="mdAndUp"
          v-show="!focusMode"
          ref="chatColumn"
          class="col col-chat"
          :style="{ flex: `0 0 ${store.chatPct}%` }"
          :topic="selectedTopic"
          :members="store.members"
          :topic-list="store.topics"
          :unread-on-open="unreadOnOpen"
          v-on="chatEvents"
        />
        <div
          v-if="mdAndUp && !focusMode"
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
          :working="working"
          :topic-list="store.topics"
          :tab="panelTab"
          :phase="phase"
          :with-chat="!mdAndUp"
          :open-card-id="openCardId"
          :agent-name="agentName"
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
              v-on="chatEvents"
            />
          </template>
        </WorkPanel>
      </div>
    </template>
  </div>
</template>

<style scoped>
/* 这条不参与伸缩：它有内容时占自己那点高度，没内容时整个不在 DOM 里，四格的高度
   都不会因为它变来变去。 */
.env-strip {
  flex: 0 0 auto;
  margin: 8px 12px 0;
}

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
</style>
