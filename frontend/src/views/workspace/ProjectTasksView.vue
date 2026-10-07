<script setup lang="ts">
// 全部任务（画面）：这个项目里的任务，一张按状态分组的列表。项目总览的「全部任务」
// 和频道下面那一行「全部任务」进的都是这一页，后者带着频道筛选。
//
// 分组的顺序是「下一步在谁手上」：待处理在最上面（下一步在人手上），然后检查中、
// 进行中、未开始。等你的那几件在行上用暖色写出来，不单独拉一组——「需要我处理」
// 只在首页「待办」一处。十四天没动静的收进最底下的「已停滞」，点开才列出来。
//
// 筛选：谁的（全部 / 我负责的 / 我协作的 / 其他人的）、哪个频道、看已关闭的。数据和
// 去处都由容器 ProjectTasks 给。
import type { MenuAction } from '@/components/common/menuAction'
import type { BoardColumn, RoomTask } from '@/cx_types'

import { computed, ref } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import AppPage from '@/components/common/AppPage.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import { t } from '@/i18n'
import { columnLabel, myPhraseLabel, phraseLabel } from '@/lib/board'
import { relTime } from '@/lib/relTime'
import { taskTitle } from '@/lib/topicState'

const props = defineProps<{
  tasks: RoomTask[]
  /** 我看得见的频道：筛选菜单列没归档的那些，任务行上写频道名也查它。 */
  channels: { id: string; title: string; archived?: boolean }[]
  /** 只看这个频道；null 是整个项目。 */
  channelId: string | null
  names: Record<string, string>
  /** handle → 头像图；没挑过的是空串，退回首字母。和 names 同源。 */
  avatars: Record<string, string>
  me: string
  loading: boolean
  failed: boolean
}>()

const emit = defineEmits<{
  (e: 'open-task', task: RoomTask): void
  (e: 'new-task'): void
  (e: 'retry'): void
  (e: 'pick-channel', channelId: string | null): void
}>()

type Whose = 'all' | 'mine' | 'helping' | 'others'
const whose = ref<Whose>('all')
const closed = ref(false)
const stalledOpen = ref(false)

const channelTitle = computed(() => new Map(props.channels.map((c) => [c.id, c.title])))
const helping = (task: RoomTask) => (task.contributor_handles ?? []).includes(props.me)
const isOpen = (task: RoomTask) => task.status === 'open' && task.presentation.column !== 'done'
const inChannel = computed(() =>
  props.channelId ? props.tasks.filter((task) => task.room_id === props.channelId) : props.tasks
)
const inView = computed(() => inChannel.value.filter((task) => isOpen(task) !== closed.value))
const belongs: Record<Whose, (task: RoomTask) => boolean> = {
  all: () => true,
  mine: (task) => task.owner_handle === props.me,
  helping,
  others: (task) => task.owner_handle !== props.me && !helping(task),
}
const counts = computed(() => ({
  all: inView.value.length,
  mine: inView.value.filter(belongs.mine).length,
  helping: inView.value.filter(belongs.helping).length,
  others: inView.value.filter(belongs.others).length,
}))
const moved = (task: RoomTask) => Date.parse(task.last_activity_at ?? task.updated_at) || 0
const shown = computed(() => inView.value.filter(belongs[whose.value]).sort((a, b) => moved(b) - moved(a)))

const ORDER: BoardColumn[] = ['needs_you', 'delivering', 'building', 'not_started']
const groups = computed(() =>
  ORDER.map((column) => ({
    column,
    tasks: shown.value.filter((task) => !task.stalled && task.presentation.column === column),
  })).filter((group) => group.tasks.length)
)
const stalled = computed(() => shown.value.filter((task) => task.stalled))

const filters: { id: Whose; label: string }[] = [
  { id: 'all', label: t('work.channelTasks.all') },
  { id: 'mine', label: t('work.channelTasks.mine') },
  { id: 'helping', label: t('work.channelTasks.helping') },
  { id: 'others', label: t('work.channelTasks.others') },
]
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
      label: `# ${c.title}`,
      icon: 'mdi-pound',
      onSelect: () => emit('pick-channel', c.id),
    })),
])
const pickedChannel = computed(() => (props.channelId ? channelTitle.value.get(props.channelId) ?? null : null))
const nameOf = (handle: string) => props.names[handle] || handle

function stateOf(task: RoomTask): { text: string; tone: 'mine' | 'running' | 'plain' } {
  if (task.awaits_me) return { text: myPhraseLabel(task.presentation.phrase), tone: 'mine' }
  if (task.presentation.phrase === 'running') return { text: phraseLabel('running'), tone: 'running' }
  return { text: phraseLabel(task.presentation.phrase), tone: 'plain' }
}
</script>

<template>
  <AppPage :title="t('work.sidebar.allTasks')" width="wide">
    <template #controls>
      <BaseButton kind="primary" size="sm" @click="emit('new-task')">{{ t('work.room.menu.newTask') }}</BaseButton>
    </template>

    <div class="tasks__filters">
      <div class="tasks__chips" role="group" :aria-label="t('work.channelTasks.whose')">
        <button
          v-for="f in filters"
          :key="f.id"
          type="button"
          class="tasks__chip t-body"
          :class="{ 'tasks__chip--on': whose === f.id }"
          :aria-pressed="whose === f.id"
          @click="whose = f.id"
        >
          {{ f.label }} <span class="tasks__count">{{ counts[f.id] }}</span>
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
            data-testid="tasks-channel"
          >
            {{ pickedChannel ? `# ${pickedChannel}` : t('work.projectTasks.allChannels') }}
            <v-icon size="16">mdi-chevron-down</v-icon>
          </button>
        </template>
      </AdaptiveMenu>
      <button
        type="button"
        class="tasks__chip t-body"
        :class="{ 'tasks__chip--on': closed }"
        :aria-pressed="closed"
        @click="closed = !closed"
      >
        {{ t('work.channelTasks.closed') }}
      </button>
    </div>

    <div v-if="loading" class="tasks__empty t-body c-muted">
      <v-progress-circular indeterminate size="20" width="2" />
    </div>
    <div v-else-if="failed" class="tasks__empty t-body c-muted" role="alert">
      {{ t('work.channelTasks.loadFailed') }}
      <button type="button" class="tasks__retry" @click="emit('retry')">
        {{ t('work.roomMachine.retry') }}
      </button>
    </div>
    <div v-else-if="!shown.length" class="tasks__empty t-body c-muted">
      {{ t('work.channelTasks.empty') }}
    </div>
    <template v-else>
      <!-- 已关闭的不再分组：它们都在「已完成」那一格。 -->
      <section
        v-for="group in closed ? [{ column: 'done', tasks: shown }] : groups"
        :key="group.column"
        class="tasks__group"
      >
        <h2 class="tasks__group-head">
          {{ columnLabel(group.column as BoardColumn) }} <span class="tasks__count">{{ group.tasks.length }}</span>
        </h2>
        <ul class="tasks__list">
          <li v-for="task in group.tasks" :key="task.id">
            <button type="button" class="tasks__row" @click="emit('open-task', task)">
              <span class="tasks__title t-body">{{ taskTitle(task) }}</span>
              <span class="tasks__channel c-faint"># {{ channelTitle.get(task.room_id) ?? '' }}</span>
              <span class="tasks__who">
                <template v-if="task.owner_handle">
                  <UserAvatar
                    :size="20"
                    :name="nameOf(task.owner_handle)"
                    :avatar="avatars[task.owner_handle] || ''"
                    :seed="task.owner_handle"
                  />
                  <span class="tasks__name">{{ nameOf(task.owner_handle) }}</span>
                </template>
                <span v-if="task.contributor_handles?.length" class="c-faint"
                  >+{{ task.contributor_handles.length }}</span
                >
              </span>
              <span class="tasks__state" :class="`tasks__state--${stateOf(task).tone}`">{{ stateOf(task).text }}</span>
              <span class="tasks__when t-meta c-faint">{{ relTime(task.last_activity_at) }}</span>
            </button>
          </li>
        </ul>
      </section>

      <section v-if="!closed && stalled.length" class="tasks__group" data-testid="tasks-stalled">
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
              <span class="tasks__title t-body">{{ taskTitle(task) }}</span>
              <span class="tasks__channel c-faint"># {{ channelTitle.get(task.room_id) ?? '' }}</span>
              <span class="tasks__who">
                <span v-if="task.owner_handle" class="tasks__name">{{ nameOf(task.owner_handle) }}</span>
              </span>
              <span class="tasks__state">{{ stateOf(task).text }}</span>
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
  overflow-x: auto;
  scrollbar-width: none;
}
.tasks__spacer {
  flex: 1 1 auto;
}
.tasks__chip {
  display: inline-flex;
  flex-shrink: 0;
  align-items: center;
  gap: 4px;
  height: 32px;
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
  border-color: var(--accent);
  background: var(--accent-wash);
  color: var(--accent-ink);
}
.tasks__count {
  color: var(--faint);
  font-weight: 400;
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
.tasks__title {
  overflow: hidden;
  color: var(--ink);
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
  color: var(--muted);
}
.tasks__state--mine {
  color: var(--accent-ink);
  font-weight: 600;
}
.tasks__state--running {
  color: var(--ok-ink);
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
/* 窄的时候一条任务两行：任务名一行，状态、频道、负责人、时间一行。 */
@media (max-width: 720px) {
  .tasks__row {
    grid-template-columns: auto auto minmax(0, 1fr);
    grid-template-areas:
      'title title title'
      'state channel who';
    gap: 4px 8px;
    padding: 10px 0;
  }
  .tasks__title {
    grid-area: title;
  }
  .tasks__state {
    grid-area: state;
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
