<script setup lang="ts">
import type { Topic } from '@/cx_types'
import type { CardPhase, TopicPhase } from '@/lib/topicState'

import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'

import { usePageTitle } from '@/composables/usePageTitle'

import TopicHeader from '@/components/TopicHeader.vue'
import WorkPanel from '@/components/WorkPanel.vue'
import { formatToolAction, isPlatformAction, toolLabel } from '@/lib/toolLabels'
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

const AUTHOR = myHandle()

const selectedTopic = computed<Topic | null>(() => store.topics.find((t) => t.id === props.topicId) ?? null)

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
} | null>(null)
const chatColumn = ref<{
  connected: boolean
  reloadAccept: (silent?: boolean) => void
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

// 对话那一栏在两端挂在不同位置（左栏 / tab 栏第一格），但接的是同一组事件。
const chatEvents = {
  'turn-done': handleTurnDone,
  'tool-used': handleToolUsed,
  'state-changed': handleStateChanged,
  'mention-click': handleMentionClick,
  'open-file': (path: string) => panelRef.value?.openFile?.(path),
  'open-resource': handleOpenResource,
  'upgrade-message': handleUpgradeMessage,
  'open-topic': openTopic,
  phase: (p: CardPhase) => (cardPhase.value = p),
  review: () => onPanelTab('changes'),
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
    chatColumn.value?.reloadAccept()
  }
}

// ⤴ 升级为话题 from a message bubble (eval A1).
async function handleUpgradeMessage(messageId: string) {
  const upgraded = await store.upgradeMessage(messageId)
  if (upgraded) openTopic(upgraded.id)
}

// Everything topic-scoped resets when the URL names a different topic.
// 「新消息从哪开始」只有开话题的那一瞬间知道：markRead 一跑，未读数就归零了。
// 所以在归零之前抓一次，交给对话栏去画那条线。
const unreadOnOpen = ref(0)
watch(
  () => props.topicId,
  (id) => {
    worklog.value = []
    working.value = false
    workingSince.value = null
    if (id) {
      unreadOnOpen.value = store.unreadMap[id] ?? 0
      store.markRead(id)
    }
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
          :worklog="worklog"
          :working="working"
          :working-since="workingSince"
          :topic-list="store.topics"
          :tab="panelTab"
          :phase="phase"
          :with-chat="!mdAndUp"
          @open-topic="openTopic"
          @mention-click="handleMentionClick"
          @update:tab="onPanelTab"
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
