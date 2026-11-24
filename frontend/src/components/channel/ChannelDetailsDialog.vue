<script setup lang="ts">
// 频道详情：点频道页头的频道名（或那一串头像，或菜单里的「频道详情」）打开。三页：
// 「关于」是名称、说明、管理者、创建时间、编号和用量；「成员」是名册（TopicRoster）；
// 「设置」是我的通知、谁能在主线发言（#4795）、新 AI 队友的默认环境、私密、归档、退出和
// 删除。频道里的人都能看，只有管理者能改（后端按同一条规则再判一次）。动作都交给外面做。
import type { ChannelDetailsTab } from '@/commands/channelDetails'
import type { MenuAction } from '@/components/common/menuAction'
import type { ProjectMemberRow, Topic, UsageStats } from '@/cx_types'
import type { ChannelSpeakers, TopicNotifyLevel } from '@/types/channels'

import { computed, ref, watch } from 'vue'

import { menuActionOf } from '@/commands'
import { notifyChoices, notifyLevelName } from '@/commands/notifyChoices'
import BaseButton from '@/components/base/BaseButton.vue'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import TopicDefaultEnvironment from '@/components/TopicDefaultEnvironment.vue'
import TopicRoster from '@/components/TopicRoster.vue'
import TopicUsageSummary from '@/components/TopicUsageSummary.vue'
import { t } from '@/i18n'
import { relTime } from '@/lib/relTime'
import { channelGlyph } from '@/lib/topicState'
import { normalizeTopicTitle, TOPIC_TITLE_MAX_LENGTH } from '@/lib/topicTitle'

const DESCRIPTION_MAX_LENGTH = 500

const props = defineProps<{
  topic: Topic
  /** 我能改这个频道：它的管理者，或者管项目的人。 */
  canManage: boolean
  /** 我管这个项目：私密频道只有这样的人能设回公开。 */
  managesProject: boolean
  /** 管这个频道的人叫什么；「综合」由项目管理员管，没有。 */
  managerName: string | null
  level: TopicNotifyLevel
  mutedUntil: string | null
  /** 项目的角色，选「谁能发言」用：key 和名字。 */
  roles: { key: string; name: string }[]
  /** 打开时落在哪一页。 */
  initialTab?: ChannelDetailsTab
  /** 频道的短编号；「综合」没有。 */
  shortId: string | null
  /** 用量：本频道、全项目两份，打开「关于」时取。 */
  usageLoading: boolean
  topicUsage: UsageStats | null
  projectUsage: UsageStats | null
  /** 「成员」那一页要的：项目名册、我是谁、我能不能拉人进来。 */
  projectMembers: ProjectMemberRow[]
  me: string
  canInvite: boolean
}>()
const open = defineModel<boolean>({ required: true })
const emit = defineEmits<{
  (e: 'rename', title: string): void
  (e: 'describe', description: string): void
  (e: 'set-private', membersOnly: boolean): void
  (e: 'archive'): void
  (e: 'unarchive'): void
  (e: 'leave'): void
  /** 「删除频道」：这里只把确认框叫起来，真正的删除由页头做。 */
  (e: 'delete'): void
  (e: 'copy-link'): void
  (e: 'notify', level: TopicNotifyLevel, until: string | null): void
  /** 谁能在主线发言（空着是所有人），以及支线是否人人能回。 */
  (e: 'speakers', roles: string[] | null, threadsOpen: boolean): void
}>()

// 谁能发言：「所有人」，或「指定角色」再勾几个。至少留一个角色；改了就存。
const speakers = computed(() => (props.topic as Topic & ChannelSpeakers).speaker_roles ?? null)
const threadsOpen = computed(() => (props.topic as Topic & ChannelSpeakers).thread_replies_open !== false)
function limitTo(mode: unknown) {
  if (mode === 'everyone') emit('speakers', null, threadsOpen.value)
  else if (!speakers.value) emit('speakers', ['admin', 'member'], threadsOpen.value)
}
function pick(keys: unknown) {
  const next = Array.isArray(keys) ? keys.map(String) : []
  if (next.length) emit('speakers', next, threadsOpen.value)
}

const TABS: ChannelDetailsTab[] = ['about', 'members', 'settings']
const tab = ref<ChannelDetailsTab>(props.initialTab ?? 'about')
watch(open, (now) => {
  if (now) {
    tab.value = props.initialTab ?? 'about'
    editing.value = null
  }
})

// 通知：「更改」点开的和频道菜单里「通知」那一层是同一份。
const notifyActions = computed<MenuAction[]>(() =>
  notifyChoices(props.level, props.mutedUntil, (level, until) => emit('notify', level, until)).map(menuActionOf)
)

const general = computed(() => props.topic.kind === 'root')
const archived = computed(() => props.topic.status === 'archived')

const editing = ref<'title' | 'description' | null>(null)
const draft = ref('')
function edit(kind: 'title' | 'description') {
  editing.value = kind
  draft.value = kind === 'title' ? props.topic.title : props.topic.description ?? ''
}
function save() {
  if (editing.value === 'title') {
    const title = normalizeTopicTitle(draft.value, props.topic.title)
    if (title && title !== props.topic.title) emit('rename', title)
  } else if (editing.value === 'description') {
    const next = draft.value.trim()
    if (next !== (props.topic.description ?? '')) emit('describe', next)
  }
  editing.value = null
}

// 私密与公开之间要先确认一次：设回公开会把全部历史给项目里所有人看。
const confirming = ref<boolean | null>(null)
const confirmOpen = computed({
  get: () => confirming.value !== null,
  set: (now: boolean) => {
    if (!now) confirming.value = null
  },
})
function confirmPrivacy() {
  if (confirming.value === null) return
  emit('set-private', confirming.value)
  confirming.value = null
}
const canMakePrivate = computed(() => props.canManage && !general.value && !archived.value && !props.topic.members_only)
const canMakePublic = computed(
  () => props.managesProject && props.topic.members_only === true && props.topic.joined === true && !archived.value
)
</script>

<template>
  <AdaptiveDialog
    v-model="open"
    size="md"
    :title="topic.title"
    :title-icon="channelGlyph(topic)"
    :cancel-label="t('global.close')"
  >
    <div role="tablist" class="details__tabs">
      <button
        v-for="name in TABS"
        :key="name"
        type="button"
        role="tab"
        class="details__tab"
        :aria-selected="tab === name"
        @click="tab = name"
      >
        {{ t(`work.channelDetails.tabs.${name}`) }}
      </button>
    </div>

    <div v-if="tab === 'about'" class="details__box" role="tabpanel">
      <div class="details__row">
        <div class="details__what">
          <span class="details__label">{{ t('work.projectSettings.channels.nameLabel') }}</span>
          <!-- eslint-disable vuejs-accessibility/no-autofocus -- the field appears because the person pressed Edit; focus moves into it -->
          <v-text-field
            v-if="editing === 'title'"
            v-model="draft"
            :maxlength="TOPIC_TITLE_MAX_LENGTH"
            variant="outlined"
            density="compact"
            hide-details
            autocomplete="off"
            autofocus
            @keydown.enter="save"
          />
          <!-- eslint-enable vuejs-accessibility/no-autofocus -->
          <span v-else data-user-content>{{ topic.title }}</span>
        </div>
        <BaseButton v-if="editing === 'title'" kind="secondary" size="sm" @click="save">{{
          t('work.channel.overview.save')
        }}</BaseButton>
        <BaseButton v-else-if="canManage && !general" kind="ghost" size="sm" @click="edit('title')">{{
          t('work.channel.overview.edit')
        }}</BaseButton>
      </div>
      <div class="details__row">
        <div class="details__what">
          <span class="details__label">{{ t('work.channel.overview.description') }}</span>
          <!-- eslint-disable vuejs-accessibility/no-autofocus -- the field appears because the person pressed Edit; focus moves into it -->
          <v-textarea
            v-if="editing === 'description'"
            v-model="draft"
            :maxlength="DESCRIPTION_MAX_LENGTH"
            variant="outlined"
            density="compact"
            rows="2"
            auto-grow
            hide-details
            autocomplete="off"
            autofocus
          />
          <!-- eslint-enable vuejs-accessibility/no-autofocus -->
          <span v-else-if="topic.description" data-user-content>{{ topic.description }}</span>
          <span v-else class="c-faint">{{ t('work.channel.overview.noDescription') }}</span>
        </div>
        <BaseButton v-if="editing === 'description'" kind="secondary" size="sm" @click="save">{{
          t('work.channel.overview.save')
        }}</BaseButton>
        <BaseButton v-else-if="canManage" kind="ghost" size="sm" @click="edit('description')">{{
          t('work.channel.overview.edit')
        }}</BaseButton>
      </div>
      <div v-if="managerName" class="details__row">
        <div class="details__what">
          <span class="details__label">{{ t('work.projectSettings.channels.columns.manager') }}</span>
          <span>{{ managerName }}</span>
        </div>
      </div>
      <div class="details__row">
        <div class="details__what">
          <span class="details__label">{{ t('work.channelDetails.created') }}</span>
          <span>{{ relTime(topic.created_at) }}</span>
        </div>
      </div>
      <div v-if="shortId" class="details__row">
        <div class="details__what">
          <span class="details__label">{{ t('work.channelDetails.number') }}</span>
          <span class="details__mono" :title="topic.id">#{{ shortId }}</span>
        </div>
        <BaseButton kind="ghost" size="sm" @click="emit('copy-link')">{{ t('work.room.menu.copyLink') }}</BaseButton>
      </div>
      <div class="details__row">
        <div class="details__what">
          <span class="details__label">{{ t('work.room.menu.usage') }}</span>
          <TopicUsageSummary :loading="usageLoading" :topic-usage="topicUsage" :project-usage="projectUsage" />
        </div>
      </div>
    </div>

    <div v-else-if="tab === 'members'" role="tabpanel">
      <TopicRoster
        :topic-id="topic.id"
        :project-id="topic.project_id"
        :project-members="projectMembers"
        :me="me"
        :can-manage="canManage"
        :can-invite="canInvite"
        :joined="topic.joined === true"
        :general="general"
      />
    </div>

    <div v-else class="details__box" role="tabpanel">
      <div v-if="topic.joined && !archived" class="details__row">
        <div class="details__what">
          <span class="details__label">{{ t('work.channel.notify.title') }}</span>
          <span class="t-meta c-muted" data-testid="details-notify-level">{{
            notifyLevelName(level, mutedUntil)
          }}</span>
        </div>
        <AdaptiveMenu :actions="notifyActions" :title="t('work.channel.notify.title')">
          <template #activator="{ props: menuProps }">
            <BaseButton v-bind="menuProps" kind="secondary" size="sm">{{ t('work.channelDetails.change') }}</BaseButton>
          </template>
        </AdaptiveMenu>
      </div>
      <div v-if="canManage && !archived" class="details__row details__row--top">
        <div class="details__what">
          <span class="details__label">{{ t('work.channelDetails.speakers') }}</span>
          <v-radio-group
            :model-value="speakers ? 'some' : 'everyone'"
            density="compact"
            hide-details
            class="details__radios"
            @update:model-value="limitTo"
          >
            <v-radio value="everyone" :label="t('work.channelDetails.speakersEveryone')" />
            <v-radio value="some" :label="t('work.channelDetails.speakersSome')" />
          </v-radio-group>
          <v-chip-group
            v-if="speakers"
            :model-value="speakers"
            multiple
            filter
            class="details__chips"
            :aria-label="t('work.channelDetails.speakersSome')"
            @update:model-value="pick"
          >
            <v-chip v-for="r in roles" :key="r.key" :value="r.key" variant="outlined" size="small">{{ r.name }}</v-chip>
          </v-chip-group>
        </div>
      </div>
      <div v-if="canManage && !archived && speakers" class="details__row">
        <div class="details__what">
          <span class="details__label">{{ t('work.channelDetails.threadsOpen') }}</span>
        </div>
        <v-switch
          density="compact"
          color="primary"
          hide-details
          :model-value="threadsOpen"
          :aria-label="t('work.channelDetails.threadsOpen')"
          @update:model-value="emit('speakers', speakers, Boolean($event))"
        />
      </div>
      <div class="details__row">
        <div class="details__what">
          <span class="details__label">{{ t('work.channelDetails.environment') }}</span>
          <TopicDefaultEnvironment :topic-id="topic.id" />
        </div>
      </div>
      <div v-if="canMakePrivate || canMakePublic" class="details__row">
        <div class="details__what">
          <span class="details__label">{{
            canMakePublic
              ? t('work.projectSettings.channels.makePublic')
              : t('work.projectSettings.channels.makePrivate')
          }}</span>
          <span class="t-meta c-muted">{{
            canMakePublic
              ? t('work.projectSettings.channels.makePublicBody')
              : t('work.projectSettings.channels.privateHint')
          }}</span>
        </div>
        <BaseButton kind="secondary" size="sm" @click="confirming = !canMakePublic">{{
          canMakePublic ? t('work.projectSettings.channels.makePublic') : t('work.projectSettings.channels.makePrivate')
        }}</BaseButton>
      </div>
      <div v-if="canManage && !general && (archived || (topic.my_tasks?.open ?? 0) === 0)" class="details__row">
        <div class="details__what">
          <span class="details__label">{{
            archived ? t('work.projectSettings.channels.unarchive') : t('work.channelDetails.archive')
          }}</span>
          <span v-if="!archived" class="t-meta c-muted">{{ t('work.channelDetails.archiveHint') }}</span>
        </div>
        <BaseButton v-if="archived" kind="secondary" size="sm" @click="emit('unarchive')">{{
          t('work.projectSettings.channels.unarchive')
        }}</BaseButton>
        <BaseButton v-else kind="danger" size="sm" @click="emit('archive')">{{
          t('work.projectSettings.channels.archive')
        }}</BaseButton>
      </div>
      <div v-if="topic.joined && !general" class="details__row">
        <div class="details__what">
          <span class="details__label">{{ t('work.channel.leave') }}</span>
        </div>
        <BaseButton kind="danger" size="sm" @click="emit('leave')">{{ t('work.channel.leave') }}</BaseButton>
      </div>
      <!-- Delete comes last: the one thing that cannot be undone does not sit among the everyday ones.
           The general channel is the project itself and cannot be deleted. -->
      <div v-if="canManage && !general" class="details__row">
        <div class="details__what">
          <span class="details__label">{{ t('work.room.menu.delete') }}</span>
          <span class="t-meta c-muted">{{ t('work.channel.delete.body') }}</span>
        </div>
        <BaseButton kind="danger" size="sm" @click="emit('delete')">{{ t('work.room.menu.delete') }}</BaseButton>
      </div>
    </div>

    <AdaptiveDialog
      v-model="confirmOpen"
      size="sm"
      :title="
        t(
          confirming
            ? 'work.projectSettings.channels.makePrivateTitle'
            : 'work.projectSettings.channels.makePublicTitle',
          { name: topic.title }
        )
      "
      :primary-label="
        confirming ? t('work.projectSettings.channels.makePrivate') : t('work.projectSettings.channels.makePublic')
      "
      @primary="confirmPrivacy"
    >
      <p class="t-body">
        {{
          confirming
            ? t('work.projectSettings.channels.makePrivateBody')
            : t('work.projectSettings.channels.makePublicBody')
        }}
      </p>
    </AdaptiveDialog>
  </AdaptiveDialog>
</template>

<style scoped>
.details__tabs {
  display: flex;
  gap: 20px;
  margin-bottom: 16px;
  border-bottom: 1px solid var(--line);
}
.details__tab {
  height: 36px;
  padding: 0;
  font: inherit;
  color: var(--muted);
  cursor: pointer;
  background: none;
  border: 0;
  border-bottom: 2px solid transparent;
}
.details__tab[aria-selected='true'] {
  font-weight: 600;
  color: var(--ink);
  border-bottom-color: var(--ink);
}
.details__box {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}
.details__row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
}
.details__row + .details__row {
  border-top: 1px solid var(--line);
}
.details__what {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.details__label {
  font-weight: 600;
  color: var(--ink);
}
.details__mono {
  font-family: var(--font-mono);
  font-size: 13px;
}
.details__row--top {
  align-items: flex-start;
}
.details__radios {
  margin-top: 4px;
}
.details__chips {
  padding-inline-start: 32px;
}
</style>
