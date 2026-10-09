<script setup lang="ts">
// 项目设置里的「频道」：只给管项目的人。项目里的每一个频道都列在这里，包括已归档的，
// 和自己不在里面的私密频道（只看得到名称、管理者、人数和最近活跃）。每一行的「⋯」里
// 管它：改名、写说明、换管理者、设为私密或公开、归档或取消归档；自己不在里面的私密
// 频道，要先加入才能改名、看里面，加入时频道里会留一行。找频道、加入频道在「浏览
// 频道」，新建频道在侧栏「频道」那颗 ＋ 上，都不在这里。
//
// 取数和不进频道也能做的两件事（换管理者、加入私密频道）由页面传进来；改名、说明、
// 私密、归档走工作区 store，侧栏当场跟着变。
import type { MenuAction } from '@/components/common/menuAction'
import type { ProjectMemberRow } from '@/cx_types'
import type { ChannelDirectory, ChannelEntry } from '@/types/channelDirectory'

import { computed, ref, watch } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import { t } from '@/i18n'
import { memberName } from '@/lib/agentNames'
import { relTime } from '@/lib/relTime'
import { normalizeTopicTitle, TOPIC_TITLE_MAX_LENGTH } from '@/lib/topicTitle'
import { useWorkspaceStore } from '@/stores/workspace'

const props = defineProps<{
  projectId: string
  /** 项目里的人，换管理者时从这里挑。 */
  members: ProjectMemberRow[]
  load: (projectId: string) => Promise<ChannelDirectory>
  stepIn: (projectId: string, channelId: string) => Promise<unknown>
  handOver: (projectId: string, channelId: string, handle: string) => Promise<unknown>
}>()
const emit = defineEmits<{ (e: 'open-channel', id: string): void }>()

const store = useWorkspaceStore()
const DESCRIPTION_MAX_LENGTH = 500

const channels = ref<ChannelEntry[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
async function reload() {
  loading.value = true
  error.value = null
  try {
    channels.value = (await props.load(props.projectId)).items
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}
watch(
  () => props.projectId,
  () => void reload(),
  { immediate: true }
)

const showArchived = ref(false)
const counts = computed(() => ({
  active: channels.value.filter((c) => !c.archived).length,
  archived: channels.value.filter((c) => c.archived).length,
}))
const rows = computed(() =>
  channels.value
    .filter((c) => c.archived === showArchived.value)
    .sort((a, b) => Number(b.general) - Number(a.general) || a.title.localeCompare(b.title))
)

const people = computed(() => props.members.filter((m) => !m.agent))
function personName(handle: string | null): string {
  if (!handle) return t('work.projectSettings.channels.projectManagers')
  const row = props.members.find((m) => m.user_handle === handle)
  return row ? memberName(row) || handle : handle
}

// 每个动作跑完都重读一遍：数字和管理者都从服务器那一份来，不在这里猜。
const failure = ref<string | null>(null)
async function act(run: () => Promise<unknown>) {
  failure.value = null
  try {
    const result = await run()
    if (result === false) return
    await reload()
  } catch (e) {
    failure.value = e instanceof Error ? e.message : String(e)
  }
}

// 改名、写说明、换管理者共用一个对话框。
type Editing = { kind: 'rename' | 'describe' | 'manager'; channel: ChannelEntry }
const editing = ref<Editing | null>(null)
const draft = ref('')
const editOpen = computed({
  get: () => editing.value !== null,
  set: (open: boolean) => {
    if (!open) editing.value = null
  },
})
function edit(kind: Editing['kind'], channel: ChannelEntry) {
  editing.value = { kind, channel }
  draft.value =
    kind === 'rename' ? channel.title : kind === 'describe' ? channel.description ?? '' : channel.manager ?? ''
}
const editTitle = computed(() =>
  editing.value
    ? t(`work.projectSettings.channels.${editing.value.kind}Title`, { name: editing.value.channel.title })
    : ''
)
const saving = ref(false)
async function saveEdit() {
  const pending = editing.value
  if (!pending || saving.value) return
  saving.value = true
  try {
    const id = pending.channel.id
    if (pending.kind === 'rename') {
      const title = normalizeTopicTitle(draft.value, pending.channel.title)
      if (title && title !== pending.channel.title) await act(() => store.renameTopic(id, title))
    } else if (pending.kind === 'describe') {
      const next = draft.value.trim()
      if (next !== (pending.channel.description ?? '')) await act(() => store.describe(id, next))
    } else if (draft.value && draft.value !== pending.channel.manager) {
      await act(() => props.handOver(props.projectId, id, draft.value))
    }
    if (!failure.value) editing.value = null
  } finally {
    saving.value = false
  }
}

// 设为私密 / 公开要先确认：设回公开会把全部历史给项目里所有人看。
const converting = ref<{ channel: ChannelEntry; membersOnly: boolean } | null>(null)
const convertOpen = computed({
  get: () => converting.value !== null,
  set: (open: boolean) => {
    if (!open) converting.value = null
  },
})
const convertingBusy = ref(false)
async function confirmConvert() {
  const pending = converting.value
  if (!pending || convertingBusy.value) return
  convertingBusy.value = true
  try {
    await act(() => store.setMembersOnly(pending.channel.id, pending.membersOnly))
    if (!failure.value) converting.value = null
  } finally {
    convertingBusy.value = false
  }
}

function actions(c: ChannelEntry): MenuAction[] {
  const list: MenuAction[] = []
  if (!c.visible) {
    if (!c.archived)
      list.push({
        key: 'step-in',
        label: t('work.projectSettings.channels.stepIn'),
        icon: 'mdi-login',
        onSelect: () => void act(() => props.stepIn(props.projectId, c.id)),
      })
  } else if (c.can_manage) {
    list.push(
      {
        key: 'rename',
        label: t('work.projectSettings.channels.rename'),
        icon: 'mdi-pencil-outline',
        onSelect: () => edit('rename', c),
      },
      {
        key: 'describe',
        label: t('work.projectSettings.channels.describe'),
        icon: 'mdi-text-box-edit-outline',
        onSelect: () => edit('describe', c),
      }
    )
  }
  if (c.can_administer)
    list.push({
      key: 'manager',
      label: t('work.projectSettings.channels.changeManager'),
      icon: 'mdi-account-switch-outline',
      onSelect: () => edit('manager', c),
    })
  if (c.visible && c.can_manage && !c.general && !c.archived)
    list.push(
      c.members_only
        ? {
            key: 'public',
            label: t('work.projectSettings.channels.makePublic'),
            icon: 'mdi-lock-open-variant-outline',
            onSelect: () => (converting.value = { channel: c, membersOnly: false }),
          }
        : {
            key: 'private',
            label: t('work.projectSettings.channels.makePrivate'),
            icon: 'mdi-lock-outline',
            onSelect: () => (converting.value = { channel: c, membersOnly: true }),
          }
    )
  if (c.can_administer)
    list.push(
      c.archived
        ? {
            key: 'unarchive',
            label: t('work.projectSettings.channels.unarchive'),
            icon: 'mdi-archive-arrow-up-outline',
            onSelect: () => void act(() => store.unarchive(c.id)),
          }
        : {
            key: 'archive',
            label: t('work.projectSettings.channels.archive'),
            icon: 'mdi-archive-outline',
            danger: true,
            onSelect: () => void act(() => store.archive(c.id)),
          }
    )
  return list
}
</script>

<template>
  <div class="channels">
    <div class="channels__filters" role="group" :aria-label="t('work.projectSettings.channels.filterLabel')">
      <button type="button" class="channels__filter t-meta" :aria-pressed="!showArchived" @click="showArchived = false">
        {{ t('work.projectSettings.channels.activeCount', { count: counts.active }) }}
      </button>
      <button type="button" class="channels__filter t-meta" :aria-pressed="showArchived" @click="showArchived = true">
        {{ t('work.projectSettings.channels.archivedCount', { count: counts.archived }) }}
      </button>
    </div>

    <p v-if="failure" role="alert" class="t-body c-danger">{{ failure }}</p>
    <BaseLoadError v-if="error" :title="t('work.channelBrowse.loadFailed')" :error="error" @retry="reload" />
    <div v-else-if="loading && !channels.length" class="py-8 text-center" role="status">
      <v-progress-circular indeterminate size="28" color="primary" />
    </div>
    <p v-else-if="!rows.length" class="t-body c-muted">{{ t('work.channelBrowse.empty') }}</p>

    <div v-else class="channels__scroll">
      <table class="channels__table">
        <thead>
          <tr class="t-meta c-faint">
            <th>{{ t('work.projectSettings.channels.columns.channel') }}</th>
            <th>{{ t('work.projectSettings.channels.columns.manager') }}</th>
            <th>{{ t('work.projectSettings.channels.columns.members') }}</th>
            <th>{{ t('work.projectSettings.channels.columns.openTasks') }}</th>
            <th>{{ t('work.projectSettings.channels.columns.active') }}</th>
            <th>
              <span class="visually-hidden">{{ t('work.projectSettings.channels.columns.actions') }}</span>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="c in rows" :key="c.id" data-testid="channel-admin-row">
            <td>
              <button v-if="c.visible" type="button" class="channels__name" @click="emit('open-channel', c.id)">
                <v-icon size="15" :icon="c.members_only ? 'mdi-lock-outline' : 'mdi-pound'" />
                <span data-user-content>{{ c.title }}</span>
              </button>
              <span v-else class="channels__name channels__name--outside">
                <v-icon size="15" icon="mdi-lock-outline" />
                <span data-user-content>{{ c.title }}</span>
              </span>
              <span v-if="c.general" class="t-meta c-faint"> {{ t('work.channelBrowse.general') }}</span>
              <span v-else-if="!c.visible" class="t-meta c-faint"> {{ t('work.projectSettings.channels.notIn') }}</span>
            </td>
            <td>{{ personName(c.manager) }}</td>
            <td>{{ c.member_count }}</td>
            <td>{{ c.open_tasks ?? '—' }}</td>
            <td class="c-muted">{{ c.last_activity_at ? relTime(c.last_activity_at) : '—' }}</td>
            <td class="channels__menu">
              <AdaptiveMenu v-if="actions(c).length" :actions="actions(c)" :title="c.title">
                <template #activator="{ props: activator }">
                  <BaseButton
                    v-bind="activator"
                    kind="ghost"
                    size="sm"
                    icon="mdi-dots-horizontal"
                    :aria-label="t('work.projectSettings.channels.actionsFor', { name: c.title })"
                  />
                </template>
              </AdaptiveMenu>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <AdaptiveDialog
      v-model="editOpen"
      size="sm"
      :title="editTitle"
      :primary-label="t('work.projectSettings.channels.save')"
      :primary-loading="saving"
      :primary-disabled="editing?.kind === 'manager' && !draft"
      :close-disabled="saving"
      @primary="saveEdit"
    >
      <v-text-field
        v-if="editing?.kind === 'rename'"
        v-model="draft"
        :label="t('work.projectSettings.channels.nameLabel')"
        :maxlength="TOPIC_TITLE_MAX_LENGTH"
        variant="outlined"
        density="comfortable"
        hide-details
        autocomplete="off"
        autofocus
      />
      <v-textarea
        v-else-if="editing?.kind === 'describe'"
        v-model="draft"
        :label="t('work.projectSettings.channels.descriptionLabel')"
        :maxlength="DESCRIPTION_MAX_LENGTH"
        variant="outlined"
        density="comfortable"
        rows="2"
        auto-grow
        hide-details
        autocomplete="off"
        autofocus
      />
      <v-select
        v-else-if="editing?.kind === 'manager'"
        v-model="draft"
        :label="t('work.projectSettings.channels.columns.manager')"
        :items="people.map((p) => ({ title: memberName(p) || p.user_handle, value: p.user_handle }))"
        variant="outlined"
        density="comfortable"
        hide-details
        autocomplete="off"
      />
    </AdaptiveDialog>

    <AdaptiveDialog
      v-model="convertOpen"
      size="sm"
      :title="
        converting
          ? t(
              converting.membersOnly
                ? 'work.projectSettings.channels.makePrivateTitle'
                : 'work.projectSettings.channels.makePublicTitle',
              { name: converting.channel.title }
            )
          : ''
      "
      :primary-label="
        converting?.membersOnly
          ? t('work.projectSettings.channels.makePrivate')
          : t('work.projectSettings.channels.makePublic')
      "
      :primary-loading="convertingBusy"
      :close-disabled="convertingBusy"
      @primary="confirmConvert"
    >
      <p class="t-body">
        {{
          converting?.membersOnly
            ? t('work.projectSettings.channels.makePrivateBody')
            : t('work.projectSettings.channels.makePublicBody')
        }}
      </p>
    </AdaptiveDialog>
  </div>
</template>

<style scoped>
.channels {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.channels__filters {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.channels__filter {
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
.channels__filter:hover {
  background: var(--fill);
}
.channels__filter[aria-pressed='true'] {
  color: var(--surface);
  background: var(--ink);
  border-color: var(--ink);
}
.channels__scroll {
  overflow-x: auto;
}
.channels__table {
  width: 100%;
  min-width: 560px;
  border-collapse: collapse;
}
.channels__table th {
  padding: 8px;
  font-weight: 400;
  text-align: left;
  border-bottom: 1px solid var(--line);
}
.channels__table td {
  padding: 10px 8px;
  border-bottom: 1px solid var(--line);
}
.channels__name {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 0;
  font: inherit;
  font-weight: 600;
  color: var(--ink);
  cursor: pointer;
  background: none;
  border: 0;
}
.channels__name--outside {
  cursor: default;
}
.channels__menu {
  width: 40px;
  text-align: right;
}
</style>
