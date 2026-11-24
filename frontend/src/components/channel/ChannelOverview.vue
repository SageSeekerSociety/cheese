<script setup lang="ts">
// 频道的概览：频道的目录，一栏窄的，自然往下滚，各段之间一条细线。从上到下是等你处理的任务
// （有才出现）、频道说明（综合是项目目标）、置顶、进行中的任务、最近关闭，然后是支线、文件、
// 例行事务各列最要紧的几行。一行一件，放不下的名字截断。点一行在它那一块里打开它；各段最后
// 那一行「全部 N 项」打开那一块的列表。频道没有自己的实况文档：要一起看的东西钉在置顶里，要
// 做成的事是任务。
import type { ConversationFile } from '@/composables/useConversationFiles'
import type { PanelDocument } from '@/composables/usePanelDoc'
import type { RoomTask, Topic } from '@/cx_types'
import type { TaskState } from '@/lib/taskState'
import type { ChannelPin } from '@/types/channels'
import type { ThreadRow } from '@/types/threads'

import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import ChannelOverviewPins from '@/components/channel/ChannelOverviewPins.vue'
import MarkdownView from '@/components/common/MarkdownView.vue'
import TaskStateMark from '@/components/task/TaskStateMark.vue'
import { t } from '@/i18n'
import { replyBlock, replySnippet, threadHeading } from '@/lib/blockDisplay'
import { RECENT_DONE } from '@/lib/channelTasks'
import { KIND_ICONS, kindOf } from '@/lib/libraryKinds'
import { relTime } from '@/lib/relTime'
import { taskState } from '@/lib/taskState'
import { taskTitle } from '@/lib/topicState'
import { myHandle } from '@/me'

const ME = myHandle()

const props = defineProps<{
  topic: Topic
  /** 综合：最上面是项目总览，不是频道说明。 */
  general: boolean
  /** 项目总览是哪一份文档；只有综合有。 */
  overview: PanelDocument | null
  /** 项目总览现在写着什么。这里只读地显示开头，改它是整份打开（`edit-overview`）。 */
  overviewText: string
  pins: ChannelPin[]
  tasks: RoomTask[]
  /** 能不能取消置顶：在主线说得上话的人。 */
  canPin: boolean
  memberNames: Record<string, string>
  agentName: string
  saveDescription: (text: string) => Promise<boolean>
  /** 这个频道的支线（没读过的在前那几行列出来）。 */
  threads?: ThreadRow[]
  /** 能不能在这个频道里开支线：支线那一段带一颗「+」。 */
  canCreateThread?: boolean
  /** 这个频道里的文件，新的在前。 */
  files?: ConversationFile[]
  /** 这个频道的例行事务：名字和什么时候跑。 */
  routines?: { id: string; title: string; when: string }[]
  /** 能不能在这个频道里新建任务（没归档）：未结束的任务那一段带一颗「+」。 */
  canCreateTask?: boolean
}>()

const emit = defineEmits<{
  (e: 'unpin', blockId: string): void
  (e: 'jump', blockId: string): void
  (e: 'open-task', taskId: string): void
  /** 等我审阅的那一件：在频道里打开它交上来的那一版。 */
  (e: 'open-delivery', taskId: string): void
  (e: 'open-all'): void
  (e: 'mention-click', handle: string): void
  (e: 'edit-overview', documentId: string): void
  (e: 'open-thread', threadId: string): void
  (e: 'new-thread'): void
  (e: 'open-file', row: ConversationFile): void
  (e: 'open-routine', routineId: string): void
  (e: 'new-routine'): void
  (e: 'new-task'): void
  /** 「全部 N 项」：打开那一块的列表。 */
  (e: 'show', key: 'threads' | 'files' | 'routines'): void
}>()

/** 支线、文件、例行事务各列几行：最要紧的那几行，其余在它那一块里。 */
const FEW = 3

/** 进行中只列最近这么多天里有动静的，最多这么多件：其余在「全部任务」里。概览答的是
 *  「这个频道现在在忙什么」，一长串很久没人碰的任务会把真在动的淹掉。 */
const LIVELY_DAYS = 7
const LIVELY_MAX = 6

const refs = computed(() => ({ mentionNames: props.memberNames, topicTitles: {} }))
const nameOf = (handle: string) => props.memberNames[handle] || handle

const open = computed(() => props.tasks.filter((task) => task.status === 'open' && task.presentation.column !== 'done'))
const movedAt = (task: RoomTask) => task.last_message?.created_at ?? task.created_at
// 等你处理：这个频道里下一步在我手上的任务，等我审阅的交付和等我回答的提问，等得最久的在前。
// 审阅按卡上的审阅人认：一张卡可以请几个人审，`needs_my_action` 只认得其中第一位。
const awaitingReview = (task: RoomTask) => task.presentation.phrase === 'awaiting_review'
const reviewsMine = (task: RoomTask) =>
  awaitingReview(task) &&
  task.card?.status === 'pending' &&
  !!myHandle() &&
  (task.card.reviewer ?? '').split('、').includes(myHandle())
const asksMe = (task: RoomTask) => task.presentation.phrase === 'awaiting_answer' && task.needs_my_action === true
const waitingOnMe = computed(() =>
  props.tasks
    .filter((task) => task.status === 'open' && (reviewsMine(task) || asksMe(task)))
    .sort((a, b) => (a.card?.since ?? a.updated_at).localeCompare(b.card?.since ?? b.updated_at))
)
// 进行中：已经列在「等你处理」里的不再列一遍。
const running = computed(() => {
  const waiting = new Set(waitingOnMe.value.map((task) => task.id))
  return open.value.filter((task) => !waiting.has(task.id))
})
function openWaiting(task: RoomTask) {
  if (awaitingReview(task)) emit('open-delivery', task.id)
  else emit('open-task', task.id)
}
const lively = computed(() => {
  const since = new Date(Date.now() - LIVELY_DAYS * 86_400_000).toISOString()
  return running.value
    .filter((task) => movedAt(task) >= since)
    .sort((a, b) => movedAt(b).localeCompare(movedAt(a)))
    .slice(0, LIVELY_MAX)
})
const recentDone = computed(() =>
  props.tasks
    .filter((task) => task.status !== 'open' || task.presentation.column === 'done')
    .sort((a, b) => (b.closed_at ?? '').localeCompare(a.closed_at ?? ''))
    .slice(0, RECENT_DONE)
)

// 支线：没读过的在前，其余照原来的次序（最近有回复的在前）。
const threadRows = computed(() => {
  const rows = props.threads ?? []
  return [...rows.filter((row) => row.unread), ...rows.filter((row) => !row.unread)].slice(0, FEW)
})
function threadName(row: ThreadRow): string {
  return row.root ? threadHeading(replyBlock(row.root), refs.value) : t('work.room.thread.title')
}
const fileRows = computed(() => (props.files ?? []).slice(0, FEW))
function fileIcon(row: ConversationFile): string {
  return row.kind === 'document' ? 'mdi-file-document-outline' : KIND_ICONS[kindOf(row.name)]
}
const routineRows = computed(() => (props.routines ?? []).slice(0, FEW))
/** 支线、文件、例行事务三段：名字、有几项、带不带「+」。 */
const lists = computed(() => [
  {
    key: 'threads' as const,
    label: t('work.room.tabs.threads'),
    count: props.threads?.length ?? 0,
    add: !!props.canCreateThread,
  },
  { key: 'files' as const, label: t('work.room.tabs.files'), count: props.files?.length ?? 0, add: false },
  { key: 'routines' as const, label: t('work.room.tabs.routines'), count: props.routines?.length ?? 0, add: true },
])
function add(key: 'threads' | 'files' | 'routines') {
  if (key === 'threads') emit('new-thread')
  else if (key === 'routines') emit('new-routine')
}
/** 一件进行中的任务那一行只有一行：最后说的一句和谁负责在悬停里。 */
function taskHint(task: RoomTask): string {
  const owner = t('work.channel.overview.owner', {
    name: nameOf(task.owner_handle ?? ''),
    when: relTime(movedAt(task)),
  })
  const second = secondOf(task)
  return [taskTitle(task), second, owner].filter(Boolean).join('\n')
}

// 状态那一格和频道里的卡是同一个记号（`lib/taskState.ts`）；它下面那一行有话要说时写它，
// 没有时写这件任务里最后说的一句。
function stateOf(task: RoomTask): TaskState {
  const agentName = (task.agent_handle && props.memberNames[task.agent_handle]) || props.agentName
  return taskState(task, { viewer: ME, nameOf, agentName })
}
function secondOf(task: RoomTask): string | null {
  return stateOf(task).line || latest(task)
}

function latest(task: RoomTask): string | null {
  const block = task.last_message
  if (!block) return null
  return t('work.channel.overview.latest', { name: nameOf(block.author), text: replySnippet(block, refs.value, 80) })
}

// ---- 项目总览：只读的开头，放不下才给「展开全文」 ----
const overviewBody = ref<HTMLElement | null>(null)
const overviewOpen = ref(false)
const overviewClipped = ref(false)
// 正文是读法加载好、画出来之后才有高度的（MarkdownView 第一次画时才加载），所以
// 量的时机跟着它长高走，不跟着字变走。
let watching: ResizeObserver | null = null
function measure() {
  const el = overviewBody.value
  overviewClipped.value = !!el && el.scrollHeight > el.clientHeight + 1
}
watch(
  overviewBody,
  (el) => {
    watching?.disconnect()
    watching = null
    if (!el) return
    if (typeof ResizeObserver !== 'undefined') {
      watching = new ResizeObserver(measure)
      watching.observe(el)
      for (const child of Array.from(el.children)) watching.observe(child)
    }
    void nextTick(measure)
  },
  { immediate: true }
)
watch(
  () => props.overviewText,
  () => void nextTick(measure)
)
onBeforeUnmount(() => watching?.disconnect())
watch(
  () => props.topic.id,
  () => (overviewOpen.value = false)
)
function onOverviewClick(event: MouseEvent) {
  const chip = (event.target as HTMLElement | null)?.closest<HTMLElement>('[data-handle]')
  if (chip?.dataset.handle) emit('mention-click', chip.dataset.handle)
}

// ---- 频道说明：管理者就地改 ----
const editing = ref(false)
const draft = ref('')
const saving = ref(false)
watch(
  () => props.topic.id,
  () => (editing.value = false)
)
function edit() {
  draft.value = props.topic.description ?? ''
  editing.value = true
}
async function save() {
  saving.value = true
  try {
    if (await props.saveDescription(draft.value.trim())) editing.value = false
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="channel-overview" data-testid="channel-overview">
    <section v-if="waitingOnMe.length" class="co-section" data-testid="channel-waiting">
      <h3 class="co-title">{{ t('work.channel.overview.waiting') }}</h3>
      <button
        v-for="task in waitingOnMe"
        :key="task.id"
        type="button"
        class="co-task"
        data-testid="channel-waiting-task"
        @click="openWaiting(task)"
      >
        <span class="co-task__head">
          <span class="co-task__title t-body">{{ taskTitle(task) }}</span>
          <TaskStateMark class="co-task__state t-meta" :state="stateOf(task)" :emphasis="true" />
        </span>
        <span v-if="stateOf(task).line" class="co-task__latest t-meta">{{ stateOf(task).line }}</span>
      </button>
    </section>
    <section v-if="general" class="co-section co-section--overview" data-testid="project-overview">
      <div class="co-head">
        <h3 class="co-title">{{ t('work.channel.overview.project') }}</h3>
        <button
          v-if="overview && overviewText.trim()"
          type="button"
          class="co-link t-meta"
          data-testid="project-overview-edit"
          @click="emit('edit-overview', overview.id)"
        >
          {{ t('work.channel.overview.editProject') }}
        </button>
      </div>
      <p class="t-meta c-faint co-note">{{ t('work.channel.overview.projectNote', { name: agentName }) }}</p>
      <template v-if="overview && overviewText.trim()">
        <!-- eslint-disable-next-line vuejs-accessibility/click-events-have-key-events, vuejs-accessibility/no-static-element-interactions -- delegates clicks on mention chips -->
        <div
          ref="overviewBody"
          class="co-overview"
          :class="{ 'co-overview--clipped': !overviewOpen }"
          @click="onOverviewClick"
        >
          <MarkdownView class="md-content" :source="overviewText" :names="refs" />
        </div>
        <button
          v-if="overviewClipped || overviewOpen"
          type="button"
          class="co-link co-more t-meta"
          @click="overviewOpen = !overviewOpen"
        >
          {{ overviewOpen ? t('work.channel.overview.collapse') : t('work.channel.overview.expand') }}
        </button>
      </template>
      <div v-else-if="overview" class="co-empty">
        <span class="t-meta c-faint">{{ t('work.channel.overview.noProject') }}</span>
        <button type="button" class="co-link t-meta" @click="emit('edit-overview', overview.id)">
          {{ t('work.channel.overview.writeProject') }}
        </button>
      </div>
    </section>
    <section v-else class="co-section">
      <div class="co-head">
        <h3 class="co-title">{{ t('work.channel.overview.description') }}</h3>
        <button v-if="topic.can_manage && !editing" type="button" class="co-link t-meta" @click="edit">
          {{ t('work.channel.overview.edit') }}
        </button>
      </div>
      <div v-if="editing" class="co-edit">
        <v-textarea
          v-model="draft"
          auto-grow
          rows="2"
          density="compact"
          hide-details
          maxlength="500"
          autocomplete="off"
          :aria-label="t('work.channel.overview.description')"
        />
        <div class="co-edit__actions">
          <BaseButton kind="primary" size="sm" :loading="saving" @click="save">
            {{ t('work.channel.overview.save') }}
          </BaseButton>
          <BaseButton kind="ghost" size="sm" @click="editing = false">{{
            t('work.channel.overview.cancel')
          }}</BaseButton>
        </div>
      </div>
      <p v-else-if="topic.description" class="t-body co-description">{{ topic.description }}</p>
      <p v-else class="t-meta c-faint">{{ t('work.channel.overview.noDescription') }}</p>
    </section>

    <ChannelOverviewPins
      :pins="pins"
      :can-pin="canPin"
      :member-names="memberNames"
      :refs="refs"
      @unpin="emit('unpin', $event)"
      @jump="emit('jump', $event)"
    />

    <section v-if="running.length || !waitingOnMe.length" class="co-section" data-testid="channel-running">
      <div class="co-head">
        <h3 class="co-title">{{ t('work.channel.overview.running', { count: lively.length }) }}</h3>
        <BaseButton
          v-if="canCreateTask"
          kind="ghost"
          size="sm"
          icon="mdi-plus"
          class="co-add"
          :title="t('work.room.menu.newTask')"
          :aria-label="t('work.room.menu.newTask')"
          data-testid="channel-new-task"
          @click="emit('new-task')"
        />
      </div>
      <p v-if="!running.length" class="t-meta c-faint">{{ t('work.channel.overview.noRunning') }}</p>
      <button
        v-for="task in lively"
        :key="task.id"
        type="button"
        class="co-row"
        data-testid="channel-task"
        :title="taskHint(task)"
        @click="emit('open-task', task.id)"
      >
        <span class="co-row__name t-body">{{ taskTitle(task) }}</span>
        <TaskStateMark class="co-row__end t-meta" :state="stateOf(task)" />
      </button>
      <button
        v-if="running.length > lively.length"
        type="button"
        class="co-link co-more t-meta"
        data-testid="channel-running-more"
        @click="emit('open-all')"
      >
        {{
          lively.length
            ? t('work.channel.overview.moreRunning', { count: running.length - lively.length })
            : t('work.channel.overview.quietRunning', { count: running.length })
        }}
      </button>
    </section>

    <section v-if="recentDone.length" class="co-section">
      <div class="co-head">
        <h3 class="co-title">{{ t('work.channel.overview.recentDone') }}</h3>
        <button type="button" class="co-link t-meta" @click="emit('open-all')">
          {{ t('work.channel.overview.allTasks') }}
        </button>
      </div>
      <button
        v-for="task in recentDone"
        :key="task.id"
        type="button"
        class="co-done"
        @click="emit('open-task', task.id)"
      >
        <v-icon
          size="14"
          class="co-done__mark"
          :title="task.closed_reason === 'neglect' ? t('work.channel.overview.closedByNeglect') : undefined"
          >{{ task.closed_reason === 'neglect' ? 'mdi-power-sleep' : 'mdi-check' }}</v-icon
        >
        <span class="co-done__title t-body">{{ taskTitle(task) }}</span>
        <span v-if="task.closed_reason === 'neglect'" class="t-meta c-faint" data-testid="closed-by-neglect">{{
          t('work.channel.overview.closedByNeglect')
        }}</span>
        <span class="t-meta c-faint">{{ relTime(task.closed_at ?? task.created_at) }}</span>
      </button>
    </section>

    <section v-for="list in lists" :key="list.key" class="co-section" :data-testid="`channel-${list.key}`">
      <div class="co-head">
        <h3 class="co-title">{{ list.label }}</h3>
        <BaseButton
          v-if="list.add"
          kind="ghost"
          size="sm"
          icon="mdi-plus"
          class="co-add"
          :title="t('work.channel.overview.add', { name: list.label })"
          :aria-label="t('work.channel.overview.add', { name: list.label })"
          @click="add(list.key)"
        />
      </div>
      <p v-if="!list.count" class="t-meta c-muted co-none">
        {{ t('work.channel.overview.none', { name: list.label }) }}
      </p>
      <template v-if="list.key === 'threads'">
        <button
          v-for="row in threadRows"
          :key="row.id"
          type="button"
          class="co-row"
          :title="threadName(row)"
          @click="emit('open-thread', row.id)"
        >
          <span
            class="co-row__unread"
            :class="{ 'co-row__unread--on': row.unread }"
            :aria-label="row.unread ? t('work.room.thread.unread') : undefined"
          />
          <span class="co-row__name t-body">{{ threadName(row) }}</span>
          <span class="co-row__end t-meta c-muted">{{ row.reply_count }}</span>
        </button>
      </template>
      <template v-else-if="list.key === 'files'">
        <button
          v-for="row in fileRows"
          :key="`${row.kind}:${row.id}`"
          type="button"
          class="co-row"
          :title="row.folder ? `${row.folder}/${row.name}` : row.name"
          @click="emit('open-file', row)"
        >
          <v-icon size="16" class="co-row__icon" :icon="fileIcon(row)" />
          <span class="co-row__name t-body">{{ row.name }}</span>
        </button>
      </template>
      <template v-else>
        <button
          v-for="row in routineRows"
          :key="row.id"
          type="button"
          class="co-row"
          :title="row.title"
          @click="emit('open-routine', row.id)"
        >
          <span class="co-row__name t-body">{{ row.title }}</span>
          <span class="co-row__end t-meta c-muted">{{ row.when }}</span>
        </button>
      </template>
      <button
        v-if="list.count"
        type="button"
        class="co-all t-meta"
        :data-testid="`channel-${list.key}-all`"
        @click="emit('show', list.key)"
      >
        {{ t('work.channel.overview.all', { count: list.count }) }}
      </button>
    </section>
  </div>
</template>

<style scoped>
.channel-overview {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  padding: 4px 16px 24px;
  overflow-y: auto;
}
/* 各段之间一条细线；置顶那一段是它自己的组件，同样算一段。 */
.channel-overview > * {
  padding: 14px 0 12px;
}
.channel-overview > * + * {
  border-top: 1px solid var(--line);
}
.co-section {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}
.co-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-height: 26px;
}
/* 段名是目录的标签：小一号的分量、次要的颜色，不和下面的一行一行抢。 */
.co-title {
  margin: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  font-weight: 500;
  color: var(--muted);
}
.co-add {
  flex: none;
  color: var(--muted);
}
.co-none {
  margin: 0;
}
/* 一行一件：名字占满，放不下截断，右边是它的状态、回复数或什么时候跑。 */
.co-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  height: 30px;
  margin: 0 -8px;
  padding: 0 8px;
  border: 0;
  border-radius: var(--radius-md);
  background: none;
  text-align: left;
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.co-row:hover {
  background: var(--fill);
}
.co-row__name {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  color: var(--text);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.co-row__end {
  flex: 0 1 auto;
  max-width: 50%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.co-row__icon {
  flex: none;
  color: var(--muted);
}
/* 没读过的回复：名字前一颗琥珀点；读过的不占位。 */
.co-row__unread {
  display: none;
  flex: none;
  width: 6px;
  height: 6px;
  border-radius: var(--radius-pill);
  background: var(--accent);
}
.co-row__unread--on {
  display: inline-block;
}
.co-all {
  align-self: flex-start;
  height: 28px;
  margin: 0 -8px;
  padding: 0 8px;
  border: 0;
  border-radius: var(--radius-md);
  background: none;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  cursor: pointer;
  transition:
    background-color var(--dur-quick) var(--ease-standard),
    color var(--dur-quick) var(--ease-standard);
}
.co-all:hover {
  background: var(--fill);
  color: var(--ink);
}
.co-note {
  margin: 0;
}
.co-overview {
  min-width: 0;
  color: var(--text);
}
/* 只给开头：大约十行，放不下的由「展开全文」接着。 */
.co-overview--clipped {
  max-height: 240px;
  overflow: hidden;
  /* A mask reads only alpha: any opaque token will do. */
  mask-image: linear-gradient(to bottom, var(--ink) 75%, transparent);
}
.co-empty {
  display: flex;
  align-items: center;
  gap: 8px;
}
.co-link {
  flex: none;
  padding: 0;
  border: 0;
  white-space: nowrap;
  background: none;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
  cursor: pointer;
  transition: color var(--dur-quick) var(--ease-standard);
}
.co-link:hover {
  color: var(--ink);
}
.co-more {
  align-self: flex-start;
  margin-top: 2px;
}
.co-description {
  margin: 0;
  color: var(--text);
  white-space: pre-wrap;
}
.co-edit {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.co-edit__actions {
  display: flex;
  gap: 8px;
}
.co-task {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin: 0 -8px;
  padding: 8px;
  border: 0;
  border-radius: var(--radius-md);
  background: none;
  text-align: left;
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.co-task:hover,
.co-done:hover {
  background: var(--fill);
}
.co-task__head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.co-task__title {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 600;
  color: var(--ink);
}
.co-task__state {
  flex: none;
}
.co-task__latest {
  display: -webkit-box;
  overflow: hidden;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  color: var(--text);
}
.co-done {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 -8px;
  padding: 4px 8px;
  border: 0;
  border-radius: var(--radius-sm);
  background: none;
  text-align: left;
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.co-done__mark {
  color: var(--ok);
}
.co-done__title {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--muted);
}
</style>
