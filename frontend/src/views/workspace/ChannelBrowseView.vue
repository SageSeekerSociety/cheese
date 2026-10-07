<script setup lang="ts">
// 「浏览频道」：项目里我能看到的频道，找、加入、退出、新建都在这一页。侧栏只列我
// 加入的频道，别的频道从这里找。数据和动作都从外面来（`ChannelBrowse.vue`）。
import type { ChannelEntry } from '@/types/channelDirectory'

import { computed, ref } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import NewChannelDialog from '@/components/channel/NewChannelDialog.vue'
import AppPage from '@/components/common/AppPage.vue'
import { t } from '@/i18n'
import { relTime } from '@/lib/relTime'

type Filter = 'all' | 'joined' | 'archived'

const props = defineProps<{
  channels: ChannelEntry[]
  loading: boolean
  error: string | null
  /** 正在加入或退出的那一个。 */
  busyId: string | null
  creating: boolean
  /** 我能新建频道（外部成员不能）。 */
  canCreate: boolean
}>()
const emit = defineEmits<{
  (e: 'open', id: string): void
  (e: 'join', id: string): void
  (e: 'leave', id: string): void
  (e: 'create', channel: { title: string; description: string; membersOnly: boolean }): void
  (e: 'retry'): void
}>()
const creatingOpen = defineModel<boolean>('creatingOpen', { default: false })

const query = ref('')
const filter = ref<Filter>('all')

// 看不到里面的私密频道（管项目的人才会拿到）不在这一页：那是项目设置里的事。
const mine = computed(() => props.channels.filter((c) => c.visible))
const counts = computed(() => ({
  all: mine.value.filter((c) => !c.archived).length,
  joined: mine.value.filter((c) => !c.archived && c.joined).length,
  archived: mine.value.filter((c) => c.archived).length,
}))

function activity(c: ChannelEntry): number {
  return c.last_activity_at ? Date.parse(c.last_activity_at) : 0
}

const shown = computed(() => {
  const needle = query.value.trim().toLowerCase()
  return mine.value
    .filter((c) => (filter.value === 'archived' ? c.archived : !c.archived))
    .filter((c) => filter.value !== 'joined' || c.joined)
    .filter(
      (c) => !needle || c.title.toLowerCase().includes(needle) || (c.description ?? '').toLowerCase().includes(needle)
    )
    .sort((a, b) => Number(b.general) - Number(a.general) || activity(b) - activity(a))
})

const FILTERS: Filter[] = ['all', 'joined', 'archived']

function meta(c: ChannelEntry): string {
  const parts: string[] = []
  if (c.joined) parts.push(t('work.channelBrowse.joined'))
  parts.push(t('work.channelBrowse.members', { count: c.member_count }))
  if (c.open_tasks) parts.push(t('work.channelBrowse.openTasks', { count: c.open_tasks }))
  if (c.last_activity_at) parts.push(t('work.channelBrowse.active', { when: relTime(c.last_activity_at) }))
  return parts.join(' · ')
}
</script>

<template>
  <AppPage :title="t('work.channel.browse')" width="wide">
    <template #controls>
      <BaseButton v-if="canCreate" kind="primary" prepend-icon="mdi-plus" @click="creatingOpen = true">
        {{ t('work.projectSettings.channels.create') }}
      </BaseButton>
    </template>

    <div class="browse">
      <v-text-field
        v-model="query"
        type="search"
        autocomplete="off"
        variant="outlined"
        density="compact"
        prepend-inner-icon="mdi-magnify"
        :placeholder="t('work.channelBrowse.search')"
        :aria-label="t('work.channelBrowse.search')"
        hide-details
      />
      <div class="browse__filters" role="group" :aria-label="t('work.channelBrowse.filterLabel')">
        <button
          v-for="f in FILTERS"
          :key="f"
          type="button"
          class="browse__filter t-meta"
          :aria-pressed="filter === f"
          @click="filter = f"
        >
          {{ t(`work.channelBrowse.filter.${f}`, { count: counts[f] }) }}
        </button>
      </div>

      <BaseLoadError v-if="error" :title="t('work.channelBrowse.loadFailed')" :error="error" @retry="emit('retry')" />
      <div v-else-if="loading && !channels.length" class="py-8 text-center" role="status">
        <v-progress-circular indeterminate size="28" color="primary" />
      </div>
      <p v-else-if="!shown.length" class="t-body c-muted">
        {{ query.trim() ? t('work.channelBrowse.noMatch') : t('work.channelBrowse.empty') }}
      </p>

      <ul v-else class="browse__list" :aria-label="t('work.channel.browse')">
        <li v-for="c in shown" :key="c.id" class="browse__row" data-testid="channel-browse-row">
          <div class="browse__main">
            <button type="button" class="browse__name" @click="emit('open', c.id)">
              <v-icon
                size="15"
                :icon="c.members_only ? 'mdi-lock-outline' : 'mdi-pound'"
                :aria-label="c.members_only ? t('work.channelBrowse.private') : undefined"
              />
              <span data-user-content>{{ c.title }}</span>
            </button>
            <span v-if="c.description" class="t-body c-muted browse__about" data-user-content>{{ c.description }}</span>
            <span v-else class="t-body c-faint">{{ t('work.channel.overview.noDescription') }}</span>
            <span class="t-meta c-faint">{{ meta(c) }}</span>
          </div>
          <span v-if="c.general" class="t-meta c-faint">{{ t('work.channelBrowse.general') }}</span>
          <template v-else-if="!c.archived">
            <BaseButton
              v-if="c.joined"
              kind="secondary"
              size="sm"
              :loading="busyId === c.id"
              :disabled="busyId !== null"
              @click="emit('leave', c.id)"
            >
              {{ t('work.channelBrowse.leave') }}
            </BaseButton>
            <BaseButton
              v-else
              kind="secondary"
              size="sm"
              :loading="busyId === c.id"
              :disabled="busyId !== null"
              @click="emit('join', c.id)"
            >
              {{ t('work.channelBrowse.join') }}
            </BaseButton>
          </template>
        </li>
      </ul>
    </div>

    <NewChannelDialog v-model="creatingOpen" :busy="creating" @create="(channel) => emit('create', channel)" />
  </AppPage>
</template>

<style scoped>
.browse {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 20px 24px 32px;
}
.browse__filters {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.browse__filter {
  height: 28px;
  padding: 0 12px;
  color: var(--muted);
  cursor: pointer;
  background: none;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-pill);
  transition:
    background-color var(--dur-quick) var(--ease-standard),
    color var(--dur-quick) var(--ease-standard);
}
.browse__filter:hover {
  background: var(--fill);
}
.browse__filter[aria-pressed='true'] {
  color: var(--surface);
  background: var(--ink);
  border-color: var(--ink);
}
.browse__list {
  display: flex;
  flex-direction: column;
  padding: 0;
  margin: 0;
  list-style: none;
  border-top: 1px solid var(--line);
}
.browse__row {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 12px 4px;
  border-bottom: 1px solid var(--line);
}
.browse__main {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.browse__name {
  display: inline-flex;
  align-items: center;
  align-self: flex-start;
  gap: 6px;
  padding: 0;
  font: inherit;
  font-size: 15px;
  font-weight: 600;
  color: var(--ink);
  cursor: pointer;
  background: none;
  border: 0;
}
.browse__name:hover {
  color: var(--accent-ink);
}
.browse__about {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
