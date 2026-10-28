<script setup lang="ts">
import type { TaskSource } from '@/types/taskOrigin'
import type { TaskOverviewHandle } from './topicView/actions'
import type { ChatColumnHandle, WorkPanelHandle } from './topicView/shared'

import { computed, defineAsyncComponent, onMounted, onUnmounted, provide, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'

import { provideAcceptCard } from '@/composables/useAcceptCard'
import { useChapters } from '@/composables/useChapters'
import { useScreenHeader } from '@/composables/useScreenHeader'

import { useTopicActions } from './topicView/actions'
import { useTopicAddress } from './topicView/address'
import { useCardPhase, useTopicConversation } from './topicView/conversation'
import { useTopicHeader } from './topicView/header'
import { useTopicLive } from './topicView/live'
import { useTopicPanelState } from './topicView/panel'

import BaseButton from '@/components/base/BaseButton.vue'
import ChannelOverview from '@/components/channel/ChannelOverview.vue'
import NewThreadDialog from '@/components/channel/NewThreadDialog.vue'
import PanelThreads from '@/components/panels/PanelThreads.vue'
import PushPermissionPrompt from '@/components/PushPermissionPrompt.vue'
import TaskHeader from '@/components/task/TaskHeader.vue'
import TaskOverview from '@/components/task/TaskOverview.vue'
import TopicAcceptCard from '@/components/TopicAcceptCard.vue'
import TopicHeader from '@/components/TopicHeader.vue'
import WorkPanel from '@/components/WorkPanel.vue'
import { t } from '@/i18n'
import { PENDING_NOTES, usePendingNotes } from '@/lib/pendingNotes'
import { warmRoutesWhenIdle } from '@/lib/routePrefetch'
import { sourceRoute } from '@/lib/taskSource'
import { taskTitle, topicTitle } from '@/lib/topicState'
import { VIEWING_CONTEXT } from '@/lib/viewingContext'
import { useWorkspaceStore } from '@/stores/workspace'
import TopicChatColumn from '@/views/workspace/TopicChatColumn.vue'
import { useChannelOverview } from '@/views/workspace/useChannelOverview'
import { useTaskOverview } from '@/views/workspace/useTaskOverview'
import { useTopicPanes } from '@/views/workspace/useTopicPanes'

// 话题视图: ONE topic header, then the chat | 工作面板 split. A task in the room
// is drawn by the same view with the task's header, its own conversation in the
// chat column and its own 概览 / 现场 / 改动 / 预览 in the panel. The input bar is
// the chat column's own — it used to span both columns from here, which read as
// addressing the whole topic while 99% of what it sent was a chat message only
// the left column shows. Which topic is open is a route param, and ProjectShell
// keys this view on it: every topic gets a fresh instance, so nothing below — or in
// any child — can carry one topic's state into the next.
//
// 这一页自己只做三件事：认地址（`topicView/address.ts`）、把两侧的把手接起来
// （`panel.ts` / `actions.ts`）、把从地址和把手推出来的那几样交给模板。按职责分出去的
// 那几块各在自己的文件里，这一页只是它们碰头的地方——所以这里能看出来的也只有页面的
// 结构，没有具体的活。
defineOptions({ name: 'TopicView' })

// 支线那一半只在打开一条支线时才要，用到时再取。
const ThreadPane = defineAsyncComponent(() => import('@/views/workspace/ThreadPane.vue'))

// `taskId`：地址指着这个房间里的一个任务时，画的是任务页。`threadId`：指着频道里的一条
// 支线时，桌面上支线占右边那一半，手机上是一整页。
const props = defineProps<{ projectId: string; topicId: string; taskId?: string; threadId?: string }>()
const { mdAndUp } = useDisplay()
const router = useRouter()
const route = useRoute()
const store = useWorkspaceStore()

/** 这一页在说的那个地址：下面几块都从它取参数，都跟着它走。 */
const here = () => props

// ---- 地址：打开的是哪一页、哪一段，以及面板开着哪一格 ----
const address = useTopicAddress({ place: here, desktop: mdAndUp })
const {
  place: selectedTopic,
  resolving,
  redirecting,
  conversationId,
  unreadOnOpen,
  panelTab,
  onPanelTab,
  panelRoutineId,
  focusBlock,
  threadFocusBlock,
  backToRoom,
  openTopic,
  jumpTo,
  onReview,
  openPlace,
  markReadOnSwitch,
} = address

// 两块要往回够的组件（面板和对话栏）和它们的参数。面板收起再打开要回到原来那一格，
// 所以它自己手里那一样也得问得到（`showing`）。
const panesRef = ref<HTMLElement | null>(null)
const panelRef = ref<WorkPanelHandle | null>(null)
const chatColumn = ref<ChatColumnHandle | null>(null)
const taskOverviewRef = ref<TaskOverviewHandle | null>(null)

// ---- 右侧面板的收 / 开（三档见 useTopicPanel）----
const panelState = useTopicPanelState({
  panes: panesRef,
  tab: panelTab,
  desktop: mdAndUp,
  thread: computed(() => !!props.threadId),
  setTab: (tab) => void router.replace({ query: { ...route.query, tab } }),
  showing: () => panelRef.value?.activeTab(),
  backToRoom,
})
const {
  open: panelOpen,
  float: panelFloat,
  close: closePanel,
  toggle: togglePanel,
  closeLabel,
  show: panelShow,
} = panelState

// ---- 左右两栏：分隔、专注模式、对话收起时的窄边（见 useTopicPanes）----
const panes = useTopicPanes({ panelOpen, panelFloat, desktop: mdAndUp })
// 右栏此刻开着哪一份文件：撰写框上「芝士会看到你在看什么」那一行读它
// （lib/viewingContext.ts）。面板收起时什么也没在看。
const viewingPath = ref<string | null>(null)
provide(
  VIEWING_CONTEXT,
  computed(() =>
    viewingPath.value && (!mdAndUp.value || panelOpen.value)
      ? { kind: 'viewing' as const, path: viewingPath.value, task_id: props.taskId ?? null }
      : null
  )
)
// 「待发批注」：右栏预览往里攒，对话输入框上方逐条列出来，一起发。整个话题共一份。
// 没有对话栏的场合（资料库、产物页那些面）预览退回自己面板底下那条。
const pendingNotes = usePendingNotes((content) => chatColumn.value?.say(content) ?? false)
provide(PENDING_NOTES, pendingNotes)

const { focusMode, focusIcon, focusLabel, docked, chatNews, noteChatNews, toggleFocus, chatStyle } = panes
const { freezeChatWidth, startPaneDrag } = panes

// 这个话题（任务）的采纳卡，整页一份：对话栏的那一条、「改动」页顶部、专注模式和手机
// 上面板底部那一条读的都是它（见 provideAcceptCard）。
const accept = provideAcceptCard({
  get topicId() {
    return props.topicId
  },
  get topicStatus() {
    return selectedTopic.value?.status ?? ''
  },
  get taskId() {
    return props.taskId ?? null
  },
})
function reloadAccept() {
  void accept.reload()
}
// 采纳卡处在哪一段：面板要按它挑页签，所以这一个词往上走。
const cardPhase = useCardPhase(accept)

// ---- 这一段对话：支线、任务页、署名（见 topicView/conversation.ts）----
const conversation = useTopicConversation({
  place: here,
  room: () => selectedTopic.value,
  onPlaceSwitch: markReadOnSwitch,
})
const {
  channelThreads,
  showThread,
  onOpenThread,
  openTaskPage,
  onOpenCard,
  upgradeMessage,
  newThreadOpen,
  creatingThread,
  canOpenThread,
  createThread,
  canRestoreThread,
  restoreAThread,
  threadTime,
  AUTHOR,
  taskPage,
  currentTask,
  taskLoading,
  taskLoadError,
  taskOwner,
  taskOpen,
  composerClosed,
  askClosed,
  roomAgents,
  memberNames,
} = conversation

// ---- 这个房间此刻是不是活的（见 topicView/live.ts）----
const live = useTopicLive({
  conversationId: () => conversationId.value,
  taskId: () => props.taskId,
  reloadTask: () => taskPage.load(true),
  noteChatNews,
  reloadAccept,
  chat: () => chatColumn.value,
})
const { working, activity, agentControl, siteTurns, activityTick, roomConnected } = live
const { handleWorking, handleTurnDone, handleStateChanged } = live

// ---- 页头和这一页的标题（见 topicView/header.ts）----
const { taskHeaderBinding, topicHeaderBinding, setDynamicTitle } = useTopicHeader({
  place: here,
  room: () => selectedTopic.value,
  task: taskPage.task,
  taskHeaderProps: taskPage.headerProps,
  memberNames,
  roomAgents,
  roomConnected,
  panelOpen,
  backToRoom,
  togglePanel,
  openTopic,
  me: AUTHOR,
})
useScreenHeader(() => {
  if (mdAndUp.value || props.threadId) return null
  const [component, binding] = props.taskId
    ? [TaskHeader, taskHeaderBinding.value]
    : [TopicHeader, topicHeaderBinding.value]
  return binding ? { content: { component, props: binding } } : null
})

// ---- 频道概览：置顶、任务、综合的项目总览 ----
const channelOverview = useChannelOverview({
  channelId: () => (props.taskId ? null : props.topicId),
  projectId: () => props.projectId,
  general: () => selectedTopic.value?.kind === 'root',
  tick: () => activityTick.value,
  reportError: (e) => store.reportError(e, t('work.channel.pins.unpinFailed')),
})
const canPin = computed(() => {
  const topic = selectedTopic.value
  return !!topic && topic.status !== 'archived' && (topic.kind === 'root' || !!topic.joined)
})

// ---- 章节：这段对话（频道主线或任务）的目录 ----
// 跟对话走：频道是频道本身，任务页是任务的 id。任务的对话里项目的人都能说话，所以
// 都能整理章节；频道主线仍然要加入了这个频道。
const chapters = useChapters({
  conversationId: () => conversationId.value,
  report: (message) => store.reportError(new Error(message), t('work.channel.chapters.unpinFailed')),
})
const canChapter = computed(() => {
  const topic = selectedTopic.value
  if (!topic || topic.status === 'archived') return false
  return !!props.taskId || topic.kind === 'root' || !!topic.joined
})

// 任务概览那一列要的：相关、这一轮的清单、第一轮失败后的重试。
const taskOverview = useTaskOverview({
  taskId: () => props.taskId,
  working: () => working.value,
  reloadTask: () => taskPage.load(true),
})
// 任务页顶上那句话：回到它所在的那段对话（频道主线、支线或另一个任务），停在它上面。
function openSource(source: TaskSource) {
  if (source.where === 'thread') channelThreads.markSeen(source.conversation_id)
  void router.push(sourceRoute(props.projectId, source))
}

// 对话栏和面板回上来说的那几件事，在这一页各归哪一处（见 topicView/actions.ts）。
const { chatEvents, onLocate, submitQuestion, askPersonal, handleMentionClick } = useTopicActions({
  place: here,
  panel: { show: panelShow },
  focusMode,
  onPanelTab,
  onReview,
  reloadAccept,
  opening: { onOpenThread, onOpenCard, upgradeMessage },
  openTopic,
  panelRef,
  chatColumn,
  taskOverviewRef,
  activity,
  agentControl,
  siteTurns,
  handleTurnDone,
  handleWorking,
  handleStateChanged,
})

// 话题画出来之后，趁浏览器空着把从这里最常去的几页的代码先下下来：总览、资料库、
// 项目文档、搜索。点过去时就只剩取数据那一段等待（lib/routePrefetch.ts）。
let cancelRouteWarm: (() => void) | null = null
onMounted(() => {
  const params = { projectId: props.projectId }
  cancelRouteWarm = warmRoutesWhenIdle(router, [
    { name: 'workspace-overview', params },
    { name: 'project-library', params },
    { name: 'project-docs', params: { ...params, kind: 'charter' } },
    { name: 'project-search', params },
  ])
})
onUnmounted(() => cancelRouteWarm?.())

void openPlace()
</script>

<template>
  <div class="topic-view d-flex flex-column fill-height" style="min-width: 0">
    <div v-if="!selectedTopic" class="flex-grow-1 d-flex align-center justify-center">
      <v-progress-circular v-if="resolving || redirecting" indeterminate color="primary" />
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
      <h1 class="visually-hidden">
        {{ taskId && currentTask ? taskTitle(currentTask) : topicTitle(selectedTopic) }}
      </h1>
      <!-- 一条话题头部，横跨对话和工作面板。任务页上是任务的那一条：同一个高度，
           写着它在哪个房间、谁负责。 -->
      <!-- 手机上这一行是顶栏的内容（script 里的 useScreenHeader；支线页见 ThreadPane）。 -->
      <template v-if="mdAndUp">
        <template v-if="taskId">
          <TaskHeader v-if="taskHeaderBinding" v-bind="taskHeaderBinding" />
        </template>
        <TopicHeader v-else-if="topicHeaderBinding" v-bind="topicHeaderBinding" />
      </template>

      <!-- 「本轮运行时间可能较长，完成后通知你」——问推送权限的那一刻。它自己决定
         什么时候出现（这一轮跑过一分钟、而且这个浏览器还没问过），平常什么都不
         画。放在这里而不是首屏：见组件自己的说明。 -->
      <PushPermissionPrompt :working="working" />

      <div v-if="taskId && !currentTask" class="task-state flex-grow-1">
        <v-progress-circular v-if="taskLoading" indeterminate color="primary" size="24" />
        <span v-else class="t-body c-muted">{{ taskLoadError ?? t('work.task.notFound') }}</span>
      </div>
      <!-- 手机：支线是一整页，← 回到频道。 -->
      <ThreadPane
        v-else-if="threadId && !mdAndUp"
        class="flex-grow-1"
        page
        :room="selectedTopic"
        :thread-id="threadId"
        :focus-block="threadFocusBlock"
        :members="store.members"
        :topic-list="store.topics"
        :member-names="memberNames"
        @title="(title: string) => setDynamicTitle(title, 'workspace-thread')"
        @close="backToRoom"
        @open-task="onOpenCard"
        @open-page="openTaskPage"
        @to-task="upgradeMessage"
        @open-file="(path: string) => panelRef?.openFile?.(path)"
        @open-topic="openTopic"
        @mention-click="handleMentionClick"
      />
      <div
        v-else
        ref="panesRef"
        :key="taskId ?? 'room'"
        class="panes d-flex flex-grow-1"
        style="min-width: 0; min-height: 0; position: relative"
      >
        <!-- 桌面：对话是左边那一栏。面板并排开着时两者之间有一条可拖的分隔，专注模式里
             这一栏像抽屉一样收起；面板收着或浮在上面时这一栏占满整宽。 -->
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
            :focus-block="focusBlock"
            :task-id="taskId ?? null"
            :task-agent-handle="currentTask?.agent_handle ?? null"
            :member-names="memberNames"
            :composer-closed="composerClosed"
            :ask-closed="askClosed"
            :accept-elsewhere="focusMode"
            :chapters="chapters.rows.value"
            :chapterable="canChapter"
            :chapter-ids="chapters.chapterIds.value"
            v-on="chatEvents"
            @chapter="chapters.add"
            @unchapter="chapters.remove"
            @rename="chapters.rename"
            @open-room="backToRoom"
          />
        </Transition>
        <!-- 专注模式里对话收成这一条窄边：点它回到并排，有新回复时带一个点。 -->
        <div v-if="mdAndUp && focusMode" class="chat-rail">
          <BaseButton
            icon="mdi-message-outline"
            size="sm"
            :title="focusLabel"
            :aria-label="focusLabel"
            @click="toggleFocus"
          />
          <span v-if="chatNews" class="chat-rail__news" aria-hidden="true" />
        </div>
        <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions -- a drag handle for the pointer; without it the split stays where it is -->
        <div
          v-if="mdAndUp && !focusMode && docked"
          class="pane-resizer"
          :title="t('work.topic.resize')"
          @mousedown.prevent="startPaneDrag"
          @dblclick="store.resetChatPct()"
        />

        <!-- 面板浮层背后的遮罩。点它收起面板——和 Esc 同一条路。 -->
        <Transition name="panel-scrim">
          <!-- eslint-disable-next-line vuejs-accessibility/click-events-have-key-events, vuejs-accessibility/no-static-element-interactions -- Esc closes the panel too -->
          <div v-if="panelFloat && panelOpen" class="panel-scrim" @click="closePanel" />
        </Transition>

        <!-- 工作面板：并排时是右边那一栏，窄档里是从右边拉进来的浮层（三档见
             useTopicPanel）。浮层收起时 visibility 一并藏掉，不进 tab 序和读屏的树。 -->
        <div
          v-show="!mdAndUp || panelFloat || panelOpen"
          id="topic-panel"
          class="col col-doc panel-host"
          :class="{ 'panel-host--sheet': panelFloat, 'panel-host--open': panelFloat && panelOpen }"
          :style="panelFloat ? undefined : { flex: '1 1 0', minWidth: 0 }"
        >
          <!-- 桌面：支线占右边这一半。工作面板只是藏起来，关掉支线回来时还停在原来那一格。 -->
          <ThreadPane
            v-if="threadId && mdAndUp"
            :room="selectedTopic"
            :thread-id="threadId"
            :focus-block="threadFocusBlock"
            :members="store.members"
            :topic-list="store.topics"
            :member-names="memberNames"
            @close="backToRoom"
            @open-task="onOpenCard"
            @open-page="openTaskPage"
            @to-task="upgradeMessage"
            @open-file="(path: string) => panelRef?.openFile?.(path)"
            @open-topic="openTopic"
            @mention-click="handleMentionClick"
          />
          <WorkPanel
            v-show="!(threadId && mdAndUp)"
            ref="panelRef"
            :submit-question="submitQuestion"
            :ask-personal="askPersonal"
            :agent-name="store.agentName"
            :agent-handle="store.agentHandle"
            :members="store.members"
            :activity="activity"
            :topic="selectedTopic"
            :task-id="taskId ?? null"
            :task-read-only="!!taskId && (!taskOwner || !taskOpen)"
            :task-has-branch="currentTask?.branch_name !== null"
            :activity-tick="activityTick"
            :working="working"
            :agent-control="agentControl"
            :site-turns="siteTurns"
            :topic-list="store.topics"
            :tab="panelTab"
            :routine-id="panelRoutineId"
            :card-phase="cardPhase"
            :with-chat="!mdAndUp"
            :compact="panelFloat || !panelOpen"
            :member-names="memberNames"
            :threads-new="channelThreads.hasNew.value"
            @open-topic="openTopic"
            @open-card="onOpenCard"
            @mention-click="handleMentionClick"
            @update:tab="onPanelTab"
            @viewing="viewingPath = $event"
            @locate="onLocate"
          >
            <!-- 页签栏右端：铺满（专注模式）和收起面板。只在桌面上并排的时候有。 -->
            <template v-if="mdAndUp && docked" #tab-actions>
              <BaseButton
                :icon="focusIcon"
                size="sm"
                :title="focusLabel"
                :aria-label="focusLabel"
                @click="toggleFocus"
              />
              <BaseButton icon="mdi-close" size="sm" :title="closeLabel" :aria-label="closeLabel" @click="closePanel" />
            </template>
            <template #overview>
              <TaskOverview
                v-if="taskId && currentTask"
                ref="taskOverviewRef"
                :room="selectedTopic"
                :task="currentTask"
                :member-names="memberNames"
                :activity-tick="activityTick"
                :topic-list="store.topics"
                :agent-name="store.agentName"
                :agent-handle="store.agentHandle"
                :members="store.members"
                :checklist="taskOverview.checklist.value"
                :related="taskOverview.related.value"
                :can-retry="taskPage.takesPart.value"
                :retrying="taskOverview.retrying.value"
                :retry-error="taskOverview.retryError.value"
                @open-topic="openTopic"
                @mention-click="handleMentionClick"
                @retry-opening="taskOverview.retryOpening"
                @open-output="(path: string) => panelRef?.openFile?.(path)"
                @open-file="(path: string) => panelRef?.openFile?.(path)"
                @open-document="(id: string, title: string) => panelRef?.openDocument?.({ id, title })"
                @open-source="openSource"
              />
              <ChannelOverview
                v-else-if="!taskId"
                :topic="selectedTopic"
                :general="selectedTopic.kind === 'root'"
                :overview="channelOverview.overview.value"
                :overview-text="channelOverview.overviewText.value"
                :pins="channelOverview.pins.value"
                :tasks="channelOverview.tasks.value"
                :can-pin="canPin"
                :member-names="memberNames"
                :agent-name="store.agentName"
                :save-description="(text: string) => store.describe(topicId, text)"
                @unpin="channelOverview.unpin"
                @edit-overview="
                  (id: string) => panelRef?.openDocument?.({ id, title: t('work.channel.overview.project') })
                "
                @jump="jumpTo"
                @open-task="onOpenCard"
                @open-all="router.push({ name: 'project-tasks', params: { projectId }, query: { channel: topicId } })"
                @mention-click="handleMentionClick"
              />
            </template>
            <template v-if="!taskId" #threads>
              <PanelThreads
                :rows="channelThreads.rows.value"
                :recycled="channelThreads.recycled.value"
                :can-restore="canRestoreThread"
                :loading="channelThreads.loading.value"
                :error="channelThreads.error.value"
                :refs="{ mentionNames: memberNames, topicTitles: {} }"
                :name-of="(handle: string) => memberNames[handle] || handle"
                :fmt-time="threadTime"
                :can-create="canOpenThread"
                @open="showThread"
                @open-task="onOpenCard"
                @new="newThreadOpen = true"
                @restore="restoreAThread"
              />
            </template>
            <!-- 手机：一屏放不下两栏，对话是 tab 栏里的第一格。 -->
            <template #chat>
              <TopicChatColumn
                ref="chatColumn"
                class="col col-chat flex-grow-1"
                :topic="selectedTopic"
                :members="store.members"
                :topic-list="store.topics"
                :unread-on-open="unreadOnOpen"
                :focus-block="focusBlock"
                :task-id="taskId ?? null"
                :task-agent-handle="currentTask?.agent_handle ?? null"
                :member-names="memberNames"
                :composer-closed="composerClosed"
                :ask-closed="askClosed"
                :chapters="chapters.rows.value"
                :chapterable="canChapter"
                :chapter-ids="chapters.chapterIds.value"
                accept-review-button
                v-on="chatEvents"
                @chapter="chapters.add"
                @unchapter="chapters.remove"
                @rename="chapters.rename"
                @open-room="backToRoom"
              />
            </template>
          </WorkPanel>
          <!-- 专注模式里对话让开了，采纳那一条跟着到面板底部：要做的决定不能跟着消失。
               手机上对话和「改动」是两个页签，在「改动」页签里决定，这一条也在底部。 -->
          <TopicAcceptCard
            v-if="selectedTopic && ((mdAndUp && focusMode) || (!mdAndUp && panelTab === 'changes'))"
            class="panel-accept"
            :topic-id="selectedTopic.id"
            :task-id="taskId ?? undefined"
            :topic-status="selectedTopic.status"
            @review="onReview"
          />
        </div>
      </div>
    </template>
    <NewThreadDialog
      v-if="newThreadOpen && selectedTopic"
      v-model="newThreadOpen"
      :busy="creatingThread"
      :channel="topicTitle(selectedTopic)"
      @create="createThread"
    />
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
.task-state {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}
</style>

<style scoped src="./topic-panes.css"></style>
