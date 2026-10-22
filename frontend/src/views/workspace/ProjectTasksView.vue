<script setup lang="ts">
// 全部任务（画面）：这个项目里的任务，一张按状态分组的列表。项目总览的「全部任务」
// 和频道下面那一行「全部任务」进的都是这一页，后者带着频道筛选。
//
// 分组的顺序是「下一步在谁手上」：待处理在最上面（下一步在人手上），然后检查中、
// 进行中、未开始。等你的那几件在行上用暖色写出来，不单独拉一组——「需要我处理」
// 只在首页「待办」一处。十四天没动静的收进最底下的「已停滞」，点开才列出来。
//
// 筛选：任务归谁、任务是否关闭、交付是否采纳、哪个频道。数据和
// 去处都由容器 ProjectTasks 给。进行中的是整份，在这里分组、筛选、数；已关闭的只会越
// 积越多，容器一页一页读（服务端筛好、数好），这里往下滚到底时要下一页（`more`）。
import type { TaskDeliveryFilter, TaskFilter } from '@/api/tasks'
import type { MenuAction } from '@/components/common/menuAction'
import type { BoardColumn, RoomTask } from '@/cx_types'
import type { TaskState } from '@/lib/taskState'

import { computed, ref } from 'vue'

import { useLoadMoreSentinel } from '@/composables/useLoadMoreSentinel'

import BaseButton from '@/components/base/BaseButton.vue'
import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import AppPage from '@/components/common/AppPage.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import TaskStateMark from '@/components/task/TaskStateMark.vue'
import { t } from '@/i18n'
import { columnLabel } from '@/lib/board'
import { relTime } from '@/lib/relTime'
import { taskState } from '@/lib/taskState'
import { taskTitle } from '@/lib/topicState'

const props = defineProps<{
  tasks: RoomTask[]
  /** 我看得见的频道：筛选菜单列没归档的那些，任务行上写频道名也查它。 */
  channels: { id: string; title: string; glyph: string; archived?: boolean }[]
  /** 只看这个频道；null 是整个项目。 */
  channelId: string | null
  names: Record<string, string>
  /** 频道 id → 坐在那个频道的队友叫什么；任务没单独指定队友时用它。 */
  roomAgentNames?: Record<string, string>
  /** 项目默认的那位 AI 队友叫什么：任务没单独指定队友时，卡住了的那一句点它的名。 */
  agentName?: string
  /** handle → 头像图；没挑过的是空串，退回首字母。和 names 同源。 */
  avatars: Record<string, string>
  me: string
  loading: boolean
  failed: boolean
  /** 已关闭的那一页页读到的（服务端已按频道、谁的筛好，最近有动静的在前）。 */
  closedTasks: RoomTask[]
  /** 已关闭的一共几件，按谁的分；还没读到是 null。 */
  closedCounts: Record<TaskFilter, number> | null
  closedHasMore: boolean
  /** 正在读下一页、或下一页读失败了。 */
  closedLoading: boolean
  closedFailed: boolean
}>()

const status = defineModel<'all' | 'open' | 'closed'>('status', { default: 'open' })
const delivery = defineModel<TaskDeliveryFilter>('delivery', { default: 'all' })
const includesClosed = computed(() => status.value !== 'open')
/** 谁的。 */
const whose = defineModel<TaskFilter>('whose', { default: 'all' })

const emit = defineEmits<{
  'open-task': [task: RoomTask]
  'new-task': []
  retry: []
  'pick-channel': [channelId: string | null]
  /** 已关闭的往下滚到底了：要下一页。 */
  more: []
}>()

const stalledOpen = ref(false)

const channelTitle = computed(() => new Map(props.channels.map((c) => [c.id, c.title])))
const helping = (task: RoomTask) => (task.contributor_handles ?? []).includes(props.me)
const isOpen = (task: RoomTask) => task.status === 'open'
const inChannel = computed(() =>
  props.channelId ? props.tasks.filter((task) => task.room_id === props.channelId) : props.tasks
)
const inView = computed(() =>
  inChannel.value.filter((task) => isOpen(task) && (delivery.value === 'all' || !!task.accepted_at))
)
const belongs: Record<TaskFilter, (task: RoomTask) => boolean> = {
  all: () => true,
  mine: (task) => task.owner_handle === props.me,
  helping,
  others: (task) => task.owner_handle !== props.me && !helping(task),
}
const openCounts = computed<Record<TaskFilter, number>>(() => ({
  all: inView.value.length,
  mine: inView.value.filter(belongs.mine).length,
  helping: inView.value.filter(belongs.helping).length,
  others: inView.value.filter(belongs.others).length,
}))
const counts = computed<Record<TaskFilter, number> | null>(() => {
  if (status.value === 'open') return openCounts.value
  if (!props.closedCounts) return null
  if (status.value === 'closed') return props.closedCounts
  return Object.fromEntries(
    Object.keys(openCounts.value).map((key) => [
      key,
      openCounts.value[key as TaskFilter] + props.closedCounts![key as TaskFilter],
    ])
  ) as Record<TaskFilter, number>
})
const moved = (task: RoomTask) => Date.parse(task.last_activity_at ?? task.updated_at) || 0
const shownOpen = computed(() =>
  status.value === 'closed' ? [] : inView.value.filter(belongs[whose.value]).sort((a, b) => moved(b) - moved(a))
)
const shownClosed = computed(() => (includesClosed.value ? props.closedTasks : []))
const shown = computed(() => [...shownOpen.value, ...shownClosed.value])

// 已关闭的滚到最底下那一行露出来时要下一页。
const sentinel = useLoadMoreSentinel(() => emit('more'))

const ORDER: BoardColumn[] = ['needs_you', 'delivering', 'building']
const groups = computed(() => {
  const openGroups = ORDER.map((column) => ({
    column,
    label: columnLabel(column),
    count: 0,
    tasks: shownOpen.value.filter(
      (task) =>
        !task.stalled &&
        (task.presentation.column === column || (column === 'building' && task.presentation.column === 'done'))
    ),
  }))
    .filter((group) => group.tasks.length)
    .map((group) => ({ ...group, count: group.tasks.length }))
  if (shownClosed.value.length)
    openGroups.push({
      column: 'done',
      label: t('work.channelTasks.closed'),
      count: props.closedCounts?.[whose.value] ?? shownClosed.value.length,
      tasks: shownClosed.value,
    })
  return openGroups
})
const stalled = computed(() => shownOpen.value.filter((task) => task.stalled))
const statuses = computed(() => [
  { id: 'all' as const, label: t('taskList.allStatuses') },
  { id: 'open' as const, label: t('taskList.open') },
  { id: 'closed' as const, label: t('work.channelTasks.closed') },
])
const deliveries = computed(() => [
  { id: 'all' as const, label: t('taskList.anyDelivery') },
  { id: 'accepted' as const, label: t('taskList.acceptedDelivery') },
])

const filters = computed<{ id: TaskFilter; label: string }[]>(() => [
  { id: 'all', label: t('work.channelTasks.all') },
  { id: 'mine', label: t('work.channelTasks.mine') },
  { id: 'helping', label: t('work.channelTasks.helping') },
  { id: 'others', label: t('work.channelTasks.others') },
])
const channelActions = computed<MenuAction[]>(() => [
  {
    key: 'all',
    label: t('work.projectTasks.allChannels'),
    icon: 'mdi-pound',
    onSelect: () => emit('pick-channel', null),
  },
  ...props.channels
    .filter((c) => !c.archived)
    .map((c) => ({
      key: c.id,
      // 符号由图标画（私密频道是锁），名字里不再带 #：两处都写就成了「# # 综合」。
      label: c.title,
      icon: c.glyph,
      onSelect: () => emit('pick-channel', c.id),
    })),
])
const pickedChannel = computed(() => (props.channelId ? channelTitle.value.get(props.channelId) ?? null : null))
const pickedGlyph = computed(() => props.channels.find((c) => c.id === props.channelId)?.glyph ?? 'mdi-pound')
const nameOf = (handle: string) => props.names[handle] || handle
const agentNameOf = (task: RoomTask) =>
  (task.agent_handle && props.names[task.agent_handle]) || props.roomAgentNames?.[task.room_id] || props.agentName || ''

// 状态那一格：图标、颜色、动不动和下面那一行都在 `lib/taskState.ts`。
function stateOf(task: RoomTask): TaskState {
  return taskState(task, { viewer: props.me, nameOf, agentName: agentNameOf(task) })
}
</script>

<template>
  <AppPage :title="t('work.sidebar.allTasks')" width="wide">
    <template #controls>
      <BaseButton kind="primary" size="sm" @click="emit('new-task')">{{ t('work.room.menu.newTask') }}</BaseButton>
    </template>

    <div class="tasks__filters">
      <div class="tasks__chips" role="group" :aria-label="t('work.channelTasks.whose')">
        <span class="tasks__filter-label t-meta">{{ t('taskList.whose') }}</span>
        <button
          v-for="f in filters"
          :key="f.id"
          type="button"
          class="tasks__chip t-body"
          :class="{ 'tasks__chip--on': whose === f.id }"
          :aria-pressed="whose === f.id"
          @click="whose = f.id"
        >
          {{ f.label }}
          <!-- Unknown counts must not look like zero tasks. -->
          <span v-if="!loading && !failed && counts" class="tasks__count">{{ counts[f.id] }}</span>
        </button>
      </div>
      <span class="tasks__spacer" />
      <AdaptiveMenu :actions="channelActions" :title="t('work.projectTasks.channel')" location="bottom end">
        <template #activator="{ props: menu }">
          <button
            v-bind="menu"
            type="button"
            class="tasks__chip t-body"
            :class="{ 'tasks__chip--on': !!channelId }"
            :data-user-content="pickedChannel || undefined"
            data-testid="tasks-channel"
          >
            <v-icon v-if="pickedChannel" size="16">{{ pickedGlyph }}</v-icon>
            {{ pickedChannel ?? t('work.projectTasks.allChannels') }}
            <v-icon size="16">mdi-chevron-down</v-icon>
          </button>
        </template>
      </AdaptiveMenu>
    </div>
    <div class="tasks__filters tasks__filters--details">
      <div class="tasks__chips" role="group" :aria-label="t('taskList.status')">
        <span class="tasks__filter-label t-meta">{{ t('taskList.status') }}</span>
        <button
          v-for="option in statuses"
          :key="option.id"
          type="button"
          class="tasks__chip t-body"
          :class="{ 'tasks__chip--on': status === option.id }"
          :aria-pressed="status === option.id"
          @click="status = option.id"
        >
          {{ option.label }}
        </button>
      </div>
      <div class="tasks__chips" role="group" :aria-label="t('taskList.delivery')">
        <span class="tasks__filter-label t-meta">{{ t('taskList.delivery') }}</span>
        <button
          v-for="option in deliveries"
          :key="option.id"
          type="button"
          class="tasks__chip t-body"
          :class="{ 'tasks__chip--on': delivery === option.id }"
          :aria-pressed="delivery === option.id"
          @click="delivery = option.id"
        >
          {{ option.label }}
        </button>
      </div>
    </div>

    <div v-if="loading" class="tasks__empty t-body c-muted">
      <v-progress-circular indeterminate size="20" width="2" />
    </div>
    <div v-else-if="failed" class="tasks__empty t-body c-muted" role="alert">
      {{ t('work.channelTasks.loadFailed') }}
      <button type="button" class="tasks__retry" @click="emit('retry')">
        {{ t('global.loadError.retry') }}
      </button>
    </div>
    <div v-else-if="!shown.length && !(includesClosed && closedFailed)" class="tasks__empty t-body c-muted">
      {{ t('work.channelTasks.empty') }}
    </div>
    <template v-else>
      <section v-for="group in groups" :key="group.column" class="tasks__group">
        <h2 class="tasks__group-head">
          {{ group.label }}
          <span class="tasks__count">{{ group.count }}</span>
        </h2>
        <ul class="tasks__list">
          <li v-for="task in group.tasks" :key="task.id">
            <button type="button" class="tasks__row" @click="emit('open-task', task)">
              <span class="tasks__head">
                <span class="tasks__title t-body" :data-user-content="task.title || undefined">{{
                  taskTitle(task)
                }}</span>
                <span v-if="stateOf(task).line" class="tasks__line" data-testid="task-state-line">{{
                  stateOf(task).line
                }}</span>
              </span>
              <span class="tasks__channel c-faint" :data-user-content="channelTitle.get(task.room_id) || undefined"
                ># {{ channelTitle.get(task.room_id) ?? '' }}</span
              >
              <span class="tasks__who">
                <template v-if="task.owner_handle">
                  <UserAvatar
                    :size="20"
                    :name="nameOf(task.owner_handle)"
                    :avatar="avatars[task.owner_handle] || ''"
                    :seed="task.owner_handle"
                  />
                  <span class="tasks__name" data-user-content>{{ nameOf(task.owner_handle) }}</span>
                </template>
                <span v-if="task.contributor_handles?.length" class="c-faint"
                  >+{{ task.contributor_handles.length }}</span
                >
              </span>
              <span class="tasks__state">
                <TaskStateMark :state="stateOf(task)" :emphasis="task.awaits_me === true" />
                <span v-if="task.accepted_at" class="tasks__accepted">{{ t('taskList.deliveryAccepted') }}</span>
              </span>
              <span class="tasks__when t-meta c-faint">{{ relTime(task.last_activity_at) }}</span>
            </button>
          </li>
        </ul>
      </section>

      <div v-if="includesClosed && closedFailed" class="tasks__empty t-body c-muted" role="alert">
        {{ t('work.channelTasks.loadFailed') }}
        <button type="button" class="tasks__retry" @click="emit('more')">
          {{ t('global.loadError.retry') }}
        </button>
      </div>
      <div v-else-if="includesClosed && closedHasMore" ref="sentinel" class="tasks__more" data-testid="tasks-more">
        <v-progress-circular v-if="closedLoading" indeterminate size="16" width="2" />
      </div>

      <section v-if="stalled.length" class="tasks__group" data-testid="tasks-stalled">
        <button
          type="button"
          class="tasks__fold t-body"
          :aria-expanded="stalledOpen"
          @click="stalledOpen = !stalledOpen"
        >
          <v-icon size="16">{{ stalledOpen ? 'mdi-chevron-down' : 'mdi-chevron-right' }}</v-icon>
          {{ t('work.projectTasks.stalled') }} <span class="tasks__count">{{ stalled.length }}</span>
          <span class="t-meta c-faint">{{ t('work.projectTasks.stalledNote') }}</span>
        </button>
        <ul v-if="stalledOpen" class="tasks__list">
          <li v-for="task in stalled" :key="task.id">
            <button type="button" class="tasks__row tasks__row--stalled" @click="emit('open-task', task)">
              <span class="tasks__title t-body" :data-user-content="task.title || undefined">{{
                taskTitle(task)
              }}</span>
              <span class="tasks__channel c-faint" :data-user-content="channelTitle.get(task.room_id) || undefined"
                ># {{ channelTitle.get(task.room_id) ?? '' }}</span
              >
              <span class="tasks__who">
                <span v-if="task.owner_handle" class="tasks__name" data-user-content>{{
                  nameOf(task.owner_handle)
                }}</span>
              </span>
              <span class="tasks__state">
                <TaskStateMark :state="stateOf(task)" />
                <span v-if="task.accepted_at" class="tasks__accepted">{{ t('taskList.deliveryAccepted') }}</span>
              </span>
              <span class="tasks__when t-meta c-faint">{{ relTime(task.last_activity_at) }}</span>
            </button>
          </li>
        </ul>
      </section>
    </template>
  </AppPage>
</template>

<style scoped>
.tasks__filters {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding-bottom: 12px;
}
.tasks__chips {
  display: flex;
  gap: 8px;
  min-width: 0;
  flex-wrap: wrap;
  align-items: center;
}
.tasks__filters--details {
  gap: 12px 24px;
}
.tasks__filter-label {
  min-width: 28px;
  color: var(--muted);
}
.tasks__spacer {
  flex: 1 1 auto;
}
.tasks__chip {
  display: inline-flex;
  flex-shrink: 0;
  align-items: center;
  gap: 4px;
  min-height: 36px;
  padding: 0 12px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-pill);
  background: var(--surface);
  color: var(--muted);
  font-family: inherit;
  white-space: nowrap;
  cursor: pointer;
}
.tasks__chip--on {
  border-color: var(--text);
  background: var(--fill);
  color: var(--ink);
  font-weight: 600;
}
.tasks__chip:focus-visible {
  outline: 2px solid var(--text);
  outline-offset: 2px;
}
.tasks__count {
  color: var(--faint);
  font-weight: 400;
}
.tasks__more {
  display: flex;
  justify-content: center;
  min-height: 32px;
  padding: 8px 0;
}
.tasks__empty {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 48px 0;
}
.tasks__retry {
  border: 0;
  background: transparent;
  color: var(--accent-ink);
  text-decoration: underline;
  cursor: pointer;
}
.tasks__group {
  margin-top: 20px;
}
.tasks__group-head {
  margin: 0;
  padding: 0 8px 8px;
  border-bottom: 1px solid var(--line-2);
  font-size: 13px;
  line-height: var(--lh-13);
  font-weight: 600;
  color: var(--ink);
}
.tasks__list {
  margin: 0;
  padding: 0;
  list-style: none;
}
.tasks__row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 140px 140px 132px 84px;
  gap: 16px;
  align-items: center;
  width: 100%;
  min-height: 48px;
  padding: 0 8px;
  border: 0;
  border-bottom: 1px solid var(--line);
  background: transparent;
  color: var(--text);
  font-family: inherit;
  text-align: left;
  cursor: pointer;
}
.tasks__row:hover {
  background: var(--fill);
}
.tasks__row--stalled .tasks__title {
  color: var(--muted);
}
.tasks__head {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.tasks__title {
  overflow: hidden;
  color: var(--ink);
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* 状态下面那一行：在做什么、在等什么、卡在哪、待谁审阅。 */
.tasks__line {
  overflow: hidden;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tasks__channel,
.tasks__who,
.tasks__state {
  font-size: 13px;
  line-height: var(--lh-13);
}
.tasks__channel,
.tasks__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tasks__who {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  color: var(--muted);
}
.tasks__state {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  color: var(--muted);
}
.tasks__accepted {
  color: var(--ok-ink);
  font-weight: 400;
}
.tasks__when {
  text-align: right;
  white-space: nowrap;
}
.tasks__fold {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  min-height: 44px;
  padding: 0 8px;
  border: 0;
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
  background: transparent;
  color: var(--muted);
  font-family: inherit;
  text-align: left;
  cursor: pointer;
}
/* Keep status and delivery readable before channel and owner on narrow screens. */
@media (max-width: 720px) {
  .tasks__filters--details {
    align-items: flex-start;
  }
  .tasks__chips {
    width: 100%;
  }
  .tasks__spacer {
    display: none;
  }
  .tasks__fold {
    flex-wrap: wrap;
  }
  .tasks__row {
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    grid-template-areas:
      'title title'
      'state state'
      'channel who';
    gap: 4px 8px;
    padding: 10px 0;
  }
  .tasks__head,
  .tasks__title {
    grid-area: title;
  }
  .tasks__state {
    grid-area: state;
    flex-direction: row;
    flex-wrap: wrap;
    gap: 4px 12px;
  }
  .tasks__channel {
    grid-area: channel;
  }
  .tasks__who {
    grid-area: who;
  }
  .tasks__when {
    display: none;
  }
}
</style>
