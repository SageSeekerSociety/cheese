<script setup lang="ts">
// 频道里有谁：频道详情的「成员」那一页。频道里的人都能从项目成员里加人、请 AI
// 队友，移人只归频道管理者和项目管理员；「综合」是项目里的所有人，那里只管
// AI 队友。AI 队友带「AI 队友」标，和 @ 菜单里一样。
//
// 每个 AI 队友的会话有自己的执行环境（#2320），写在它那一行下面，各改各的。新会话
// 默认用的环境在详情的「设置」里（TopicDefaultEnvironment）。页头那一串头像是
// TopicMembers，点它打开的就是这一页。
import type { ProjectMemberRow, TopicMemberRow } from '../cx_types'
import type { SessionEnvironment } from '../types/compute'
import type { MenuAction } from './common/menuAction'

import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { useRowMenu } from '../composables/useRowMenu'
import { useTopicRoster } from '../composables/useTopicRoster'
import { t } from '../i18n'
import { memberName, shownHandle } from '../lib/agentNames'
import { environmentName } from '../lib/environment'
import { externalHandles } from '../lib/externalMembers'
import { whenIdle } from '../lib/idle'
import { getAvatarUrl } from '../utils/materials'

import AdaptiveMenu from './common/AdaptiveMenu.vue'
import ExternalTag from './common/ExternalTag.vue'
import LoadingSkeleton from './common/LoadingSkeleton.vue'
import UserAvatar from './common/UserAvatar.vue'
import CheeseAvatar from './CheeseAvatar.vue'
import EnvironmentMenu from './EnvironmentMenu.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'

const props = defineProps<{
  topicId: string
  projectId: string
  projectMembers: ProjectMemberRow[]
  me: string
  /** 我管不管这个频道（创建者或项目管理员）。 */
  canManage: boolean
  /** 不管频道也能加人：频道里角色允许的人都能加人，移出仍只归管理者。 */
  canInvite?: boolean
  /** 我在这个频道里：加不了人时，说的是角色不允许，而不是人不在频道里。 */
  joined?: boolean
  /** 这是「综合」：项目里的人都在，名单上只有 AI 队友能加减。 */
  general?: boolean
}>()

// 名册本身、加人移人两条动作、以及每位队友的执行环境，都在这一层
// （`.claude/rules/architecture.md`：`src/components/**` 不碰 API 层）。
const {
  members,
  loading,
  loadError,
  busy,
  actionError,
  addMember,
  removeMember,
  machines,
  machinesError,
  loadMachines,
  saveSession,
} = useTopicRoster(() => props.topicId)

const addHandle = ref<string | null>(null)

// 改名册那一下没成，压过「名册没读到」：那是此刻手上这一下的结果。
const shownError = computed(() => actionError.value || loadError.value)

// 名册一打开就把整份名单一次建出来：几十行，每行一个头像组件，平板上一等就是几秒，
// 这中间屏幕上一个像素都不动，看着像没点到。列表本身只有 320px 高、一次看五六行，
// 所以先挂看得见的那几行，其余趁浏览器空下来一批一批补——第一帧立刻有内容。
const FIRST_ROWS = 8
const ROWS_PER_BATCH = 24
const shownRows = ref(FIRST_ROWS)
const shownMembers = computed(() => members.value.slice(0, shownRows.value))
let fillRows: (() => void) | null = null
let readEnv: (() => void) | null = null

function stopFilling(): void {
  fillRows?.()
  fillRows = null
}

function fillRest(): void {
  fillRows = null
  shownRows.value = Math.min(members.value.length, shownRows.value + ROWS_PER_BATCH)
  if (shownRows.value < members.value.length) fillRows = whenIdle(fillRest)
}

// 加人、移人之后名单长度会变：还有没挂上的行时，补行接着走，别让新加的人一直不出现。
watch(
  () => members.value.length,
  () => {
    if (fillRows !== null) return
    if (shownRows.value < members.value.length) fillRows = whenIdle(fillRest)
  }
)

onMounted(() => {
  if (members.value.length > FIRST_ROWS) fillRows = whenIdle(fillRest)
  // 每位队友跑在哪个环境：排在建行后面，别和第一帧抢。
  readEnv = whenIdle(loadMachines)
})

onBeforeUnmount(() => {
  stopFilling()
  readEnv?.()
  readEnv = null
})
// 一位 AI 队友在这里的会话：按它坐的那个名册位认（成员自己的 Claude Code 坐在主人
// 名下的位上，会话的 agent_handle 和名册上的 handle 不一定相同）。
function sessionsOf(handle: string): SessionEnvironment[] {
  return machines.value?.sessions.filter((s) => s.seat_handle === handle) ?? []
}

// 「位」而不是「人」：同一句话要数得下一个 AI 队友。
const countLabel = computed(() => t('work.room.roster.count', { count: members.value.length }))

// 谁能被移出：管理者能移出任何一位，只是「综合」里的人不在这份名单上的话就移不出——
// 那里的人是项目成员，要离开得退出项目。AI 队友在哪都能移出。
function removable(m: TopicMemberRow): boolean {
  return props.canManage && (m.agent === true || !props.general)
}

// 右键一位成员：行里那颗移出按钮，弹在鼠标那一点上。
const rowMenu = useRowMenu<string>()
function memberActions(m: TopicMemberRow): MenuAction[] {
  return [
    {
      key: 'remove',
      label: t('work.room.roster.remove'),
      icon: 'mdi-account-remove-outline',
      danger: true,
      disabled: busy.value,
      onSelect: () => void onRemove(m.member_handle),
    },
  ]
}

// 项目里的外部成员（团队以外、被邀请进来的人）。房间名册上的人都来自项目名册，所以
// 谁是外部成员问项目名册就够了，列表和「添加」下拉都挂「外部」。
const externals = computed(() => externalHandles(props.projectMembers))

// 还不在这间房里的项目成员——「添加」那个下拉。只列项目名册上的人：房间只能从项目的
// 成员里挑，团队以外的人得先被邀请成外部成员。请一个 AI 队友进房间和请一个人
// 是同一件事（往名册上加一行），所以它们本来就在同一张项目名册上，这里不再把两
// 份拼起来。已停用的队友不列：停用就是为了挡住新的邀请。
const addable = computed(() => {
  const inRoom = new Set(members.value.map((m) => m.member_handle))
  return (
    props.projectMembers
      // 成员自己的 Claude Code 不由别人请进来：主人第一次 @ 它时它自己入座（#2991）。
      .filter((m) => !inRoom.has(m.user_handle) && m.active !== false && !m.owner_handle)
      // 「综合」里本来就有项目里的每个人，只剩 AI 队友可请。
      .filter((m) => !props.general || m.agent)
      .map((m) => ({
        title: memberName(m) || m.user_handle,
        value: m.user_handle,
        handle: shownHandle(m.user_handle, m),
        agent: !!m.agent,
        external: externals.value.has(m.user_handle),
        face: m.avatar_id != null ? getAvatarUrl(m.avatar_id) : '',
      }))
  )
})

async function onAdd() {
  const handle = addHandle.value
  if (!handle) return
  await addMember(handle)
  addHandle.value = null
}

async function onRemove(handle: string) {
  await removeMember(handle)
}
</script>

<template>
  <div class="roster">
    <div class="roster__head">
      <span class="roster__count">{{ countLabel }}</span>
    </div>

    <div v-if="shownError" class="roster__error">{{ shownError }}</div>

    <LoadingSkeleton v-if="loading" variant="roster" />
    <BaseEmptyState
      v-else-if="!members.length && !shownError"
      size="inline"
      class="roster__empty"
      :title="t('work.room.roster.empty')"
    />
    <ul v-else class="roster__list">
      <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions -- right-click opens a menu whose one action is the row's × button -->
      <li
        v-for="m in shownMembers"
        :key="m.member_handle"
        class="roster__item"
        @contextmenu="removable(m) && rowMenu.open(m.member_handle, $event)"
      >
        <AdaptiveMenu
          v-if="removable(m)"
          v-bind="rowMenu.bind(m.member_handle)"
          :actions="memberActions(m)"
          :title="memberName(m) || m.member_handle"
        >
          <template #activator />
        </AdaptiveMenu>
        <CheeseAvatar v-if="m.agent" :size="26" :name="memberName(m) || m.member_handle" :handle="m.member_handle" />
        <UserAvatar
          v-else
          class="roster__avatar"
          :size="26"
          :name="memberName(m) || m.member_handle"
          :seed="m.member_handle"
          :avatar="m.avatar_id != null ? getAvatarUrl(m.avatar_id) : ''"
        />
        <span class="roster__who">
          <span class="roster__name">{{ memberName(m) || m.member_handle }}</span>
          <span class="roster__handle">{{ shownHandle(m.member_handle, m) }}</span>
        </span>
        <span v-if="m.agent" class="roster__badge">{{ t('work.room.roster.agentBadge') }}</span>
        <ExternalTag v-else-if="externals.has(m.member_handle)" />

        <!-- 管这个频道的人标一个「管理者」：这个人和项目管理员一起管这个频道。
             换人不在这个频道里做，只有项目设置那张频道表能换，所以在标记旁边直说去处
             ——名单里找不到「更换管理员」那颗按钮，人得知道去哪儿找（清单第 5 条）。 -->
        <span v-if="m.role === 'owner' && !m.agent" class="roster__role">{{ t('work.room.roster.creator') }}</span>
        <span v-if="m.role === 'owner' && !m.agent" class="roster__manager-hint">{{
          t('work.room.roster.managerHint')
        }}</span>
        <button
          v-if="removable(m)"
          type="button"
          class="roster__remove"
          :disabled="busy"
          :title="t('work.room.roster.remove')"
          @click="onRemove(m.member_handle)"
        >
          <v-icon size="15">mdi-close</v-icon>
        </button>
        <div
          v-for="session in m.agent ? sessionsOf(m.member_handle) : []"
          :key="session.session_id"
          class="roster__env"
          :data-testid="`session-env-${session.session_id}`"
        >
          <span class="roster__machine-text">{{
            session.environment
              ? environmentName(session.environment, machines?.devices, machines?.specs)
              : t('compute.env.notStarted')
          }}</span>
          <EnvironmentMenu
            v-if="machines"
            :heading="t('compute.env.sessionHeading', { name: memberName(m) || m.member_handle })"
            :hint="t('compute.env.sessionHint')"
            :current="session.environment ?? machines.default"
            :devices="machines.devices"
            :specs="machines.specs"
            :save="saveSession(session)"
            @changed="loadMachines"
          />
        </div>
      </li>
    </ul>

    <div v-if="machinesError" class="roster__hint">
      {{ t('compute.env.loadFailed') }}
      <button type="button" class="roster__retry" @click="loadMachines">{{ t('global.loadError.retry') }}</button>
    </div>

    <!-- 频道里的人从项目成员里加人；「综合」里只剩 AI 队友可加。 -->
    <div v-if="canManage || canInvite" class="roster__add">
      <v-select
        v-model="addHandle"
        autocomplete="off"
        :items="addable"
        density="compact"
        variant="outlined"
        hide-details
        :placeholder="t('work.room.roster.addPlaceholder')"
        :no-data-text="t('work.room.roster.allIn')"
        class="roster__select"
      >
        <!-- 每一行和上面名册里那一行同一个样子：头像、名字、@handle、标。只有一串
             名字的话，好几个「芝士X」分不出谁是谁。 -->
        <template #item="{ props: ip, item }">
          <v-list-item v-bind="ip" :title="undefined" class="roster__option">
            <template #prepend>
              <CheeseAvatar v-if="item.raw.agent" :size="26" :name="item.raw.title" :handle="item.raw.value" />
              <UserAvatar
                v-else
                class="roster__avatar"
                :size="26"
                :name="item.raw.title"
                :seed="item.raw.value"
                :avatar="item.raw.face"
              />
            </template>
            <span class="roster__who">
              <span class="roster__name">{{ item.raw.title }}</span>
              <span class="roster__handle">{{ item.raw.handle }}</span>
            </span>
            <template #append>
              <span v-if="item.raw.agent" class="roster__badge">{{ t('work.room.roster.agentBadge') }}</span>
              <ExternalTag v-else-if="item.raw.external" />
            </template>
          </v-list-item>
        </template>
      </v-select>
      <BaseButton kind="secondary" size="sm" :disabled="!addHandle || busy" :loading="busy" @click="onAdd">
        {{ t('work.room.roster.add') }}
      </BaseButton>
    </div>
    <div v-else class="roster__hint">
      {{ joined ? t('work.room.roster.readOnlyRole') : t('work.room.roster.readOnly') }}
    </div>
  </div>
</template>

<style scoped>
.roster {
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}
.roster__head {
  display: flex;
  align-items: baseline;
  padding: 10px 14px;
  border-bottom: 1px solid var(--line-2);
}
.roster__count {
  font-size: 12px;
  color: var(--muted);
}
.roster__error {
  padding: 8px 14px;
  font-size: 13px;
  color: rgb(var(--v-theme-error, 211, 47, 47));
  background: rgba(var(--v-theme-error, 211, 47, 47), 0.08);
}
.roster__empty {
  padding: 12px 14px;
}
.roster__hint {
  padding: 12px 14px;
  font-size: 13px;
  color: var(--muted);
}
.roster__list {
  list-style: none;
  margin: 0;
  padding: 4px 0;
  max-height: 320px;
  overflow-y: auto;
}
.roster__item {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 9px;
  padding: 6px 14px;
}
/* AI 队友那一行底下：它这条会话的环境，占满一整行，和名字对齐。 */
.roster__env {
  display: flex;
  flex-basis: 100%;
  align-items: center;
  gap: 8px;
  min-width: 0;
  padding-left: 35px;
  color: var(--muted);
  font-size: 13px;
}
.roster__machine-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.roster__retry {
  border: 0;
  background: transparent;
  color: var(--text);
  text-decoration: underline;
  cursor: pointer;
}
.roster__item:hover {
  background: var(--fill);
}
.roster__avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  /* 人是圆的；AI 队友那一行画的是 CheeseAvatar。 */
  border-radius: var(--radius-pill);
  font-size: 0.72rem;
  font-weight: 700;
  color: var(
    --avatar-ink
  ); /* theme-invariant ground: UserAvatar's colour comes from avatarColor(), the same in both themes */
  flex: none;
  overflow: hidden;
}
/* 「添加成员」下拉里的一行：头像和名字之间留出和名册一样的间距。 */
.roster__option :deep(.v-list-item__prepend) {
  margin-inline-end: 10px;
}
.roster__option :deep(.v-list-item__spacer) {
  display: none;
}
.roster__who {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1 1 auto;
}
.roster__name {
  font-size: 13px;
  font-weight: 500;
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.roster__handle {
  font-size: 12px;
  color: var(--muted);
}
.roster__badge {
  font-size: 12px;
  font-weight: 600;
  padding: 1px 5px;
  border-radius: var(--radius-sm);
  color: rgb(var(--v-theme-primary));
  background: rgba(var(--v-theme-primary), 0.12);
}
.roster__role {
  font-size: 12px;
  color: var(--muted);
  flex: none;
}
/* 「在项目设置的频道表里更换」：去处比标记本身还次要，所以更淡、更小。 */
.roster__manager-hint {
  font-size: 12px;
  color: var(--faint);
  flex: none;
}
.roster__remove {
  display: inline-flex;
  align-items: center;
  color: var(--muted);
  cursor: pointer;
  flex: none;
}
.roster__remove:hover:not(:disabled) {
  color: rgb(var(--v-theme-error, 211, 47, 47));
}
.roster__remove:disabled {
  opacity: 0.3;
  cursor: default;
}
.roster__add {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  border-top: 1px solid var(--line-2);
}
.roster__select {
  flex: 1 1 auto;
}
</style>
