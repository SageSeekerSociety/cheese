<script setup lang="ts">
// 一个频道里的全部任务（画面）。侧栏只挂和我有关的几条（`lib/railTasks`），其余的在
// 这里：按「谁的」筛（全部 / 我负责的 / 我协作的 / 其他人的），默认只看还在进行的，
// 按最近有动静的排。数据和去处都由容器 ChannelTasks 给。
import type { RoomTask } from '@/cx_types'

import { computed, ref } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import AppPage from '@/components/common/AppPage.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import { t } from '@/i18n'
import { phraseLabel } from '@/lib/board'
import { relTime } from '@/lib/relTime'
import { taskTitle } from '@/lib/topicState'

const props = defineProps<{
  channelTitle: string | null
  tasks: RoomTask[]
  names: Record<string, string>
  me: string
  loading: boolean
  failed: boolean
}>()

const emit = defineEmits<{
  (e: 'open-task', task: RoomTask): void
  (e: 'new-task'): void
  (e: 'retry'): void
}>()

type Whose = 'all' | 'mine' | 'helping' | 'others'
const whose = ref<Whose>('all')
const closed = ref(false)

const helping = (task: RoomTask) => (task.contributor_handles ?? []).includes(props.me)
const isOpen = (task: RoomTask) => task.status === 'open' && task.presentation.column !== 'done'
const inView = computed(() => props.tasks.filter((task) => isOpen(task) !== closed.value))
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
const filters: { id: Whose; label: string }[] = [
  { id: 'all', label: t('work.channelTasks.all') },
  { id: 'mine', label: t('work.channelTasks.mine') },
  { id: 'helping', label: t('work.channelTasks.helping') },
  { id: 'others', label: t('work.channelTasks.others') },
]
const nameOf = (handle: string) => props.names[handle] || handle
</script>

<template>
  <AppPage
    :title="t('work.sidebar.allTasks')"
    width="wide"
    :parent="channelTitle ? { label: `# ${channelTitle}`, to: { name: 'workspace-topic' } } : undefined"
  >
    <template #controls>
      <BaseButton v-if="channelTitle" kind="primary" size="sm" @click="emit('new-task')">{{
        t('work.room.menu.newTask')
      }}</BaseButton>
    </template>

    <div class="channel-tasks__filters" role="group" :aria-label="t('work.channelTasks.whose')">
      <button
        v-for="f in filters"
        :key="f.id"
        type="button"
        class="channel-tasks__chip t-body"
        :class="{ 'channel-tasks__chip--on': whose === f.id }"
        :aria-pressed="whose === f.id"
        @click="whose = f.id"
      >
        {{ f.label }} <span class="channel-tasks__count">{{ counts[f.id] }}</span>
      </button>
      <span class="channel-tasks__spacer" />
      <button
        type="button"
        class="channel-tasks__chip t-body"
        :class="{ 'channel-tasks__chip--on': closed }"
        :aria-pressed="closed"
        @click="closed = !closed"
      >
        {{ t('work.channelTasks.closed') }}
      </button>
    </div>

    <div v-if="loading" class="channel-tasks__empty t-body c-muted">
      <v-progress-circular indeterminate size="20" width="2" />
    </div>
    <div v-else-if="failed" class="channel-tasks__empty t-body c-muted" role="alert">
      {{ t('work.channelTasks.loadFailed') }}
      <button type="button" class="channel-tasks__retry" @click="emit('retry')">
        {{ t('work.roomMachine.retry') }}
      </button>
    </div>
    <div v-else-if="!shown.length" class="channel-tasks__empty t-body c-muted">
      {{ t('work.channelTasks.empty') }}
    </div>
    <ul v-else class="channel-tasks__list">
      <li v-for="task in shown" :key="task.id">
        <button type="button" class="channel-tasks__row" @click="emit('open-task', task)">
          <span class="channel-tasks__title t-body">{{ taskTitle(task) }}</span>
          <span class="channel-tasks__who t-body">
            <template v-if="task.owner_handle">
              <UserAvatar :size="20" :name="nameOf(task.owner_handle)" />
              <span>{{ nameOf(task.owner_handle) }}</span>
            </template>
            <span v-if="task.contributor_handles?.length" class="c-faint">+{{ task.contributor_handles.length }}</span>
          </span>
          <span class="channel-tasks__state t-body c-muted">{{ phraseLabel(task.presentation.phrase) }}</span>
          <span class="channel-tasks__when t-meta c-faint">{{ relTime(task.last_activity_at) }}</span>
        </button>
      </li>
    </ul>
  </AppPage>
</template>

<style scoped>
.channel-tasks__filters {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--line);
}
.channel-tasks__spacer {
  flex: 1 1 auto;
}
.channel-tasks__chip {
  height: 30px;
  padding: 0 12px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-pill);
  background: var(--surface);
  color: var(--muted);
  cursor: pointer;
}
.channel-tasks__chip--on {
  border-color: var(--accent);
  background: var(--accent-wash);
  color: var(--accent-ink);
}
.channel-tasks__count {
  color: var(--faint);
}
.channel-tasks__empty {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 48px 0;
}
.channel-tasks__retry {
  border: 0;
  background: transparent;
  color: var(--accent-ink);
  text-decoration: underline;
  cursor: pointer;
}
.channel-tasks__list {
  margin: 0;
  padding: 0;
  list-style: none;
}
.channel-tasks__row {
  font-family: inherit;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 180px 120px 90px;
  gap: 16px;
  align-items: center;
  width: 100%;
  min-height: 48px;
  padding: 0 8px;
  border: 0;
  border-bottom: 1px solid var(--line);
  background: transparent;
  color: var(--text);
  text-align: left;
  cursor: pointer;
}
.channel-tasks__row:hover {
  background: var(--fill);
}
.channel-tasks__title {
  overflow: hidden;
  color: var(--ink);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.channel-tasks__who {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
@media (max-width: 720px) {
  .channel-tasks__row {
    grid-template-columns: minmax(0, 1fr) auto;
    padding: 8px;
  }
  .channel-tasks__who,
  .channel-tasks__when {
    display: none;
  }
}
</style>
