<script setup lang="ts">
// 频道里有谁：页头一小串头像，点开是名单。频道里的人都能从项目成员里加人、请 AI
// 队友，移人只归频道管理者和项目管理员；「综合」是项目里的所有人，那里只管
// AI 队友。AI 队友带「AI 队友」标，和 @ 菜单里一样。
//
// 每个 AI 队友的会话有自己的执行环境（#2320），写在它那一行下面，各改各的；名册
// 底下一行是新会话默认用的环境，改它不搬已经开工的会话。
import type { ProjectMemberRow, TopicMemberRow } from '../cx_types'
import type { Environment, SessionEnvironment, TopicEnvironment } from '../types/compute'
import type { MenuAction } from './common/menuAction'

import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useQuery } from '@tanstack/vue-query'

import { addTopicMember, removeTopicMember, setEnvironmentDefault, setSessionEnvironment } from '../api'
import { useRowMenu } from '../composables/useRowMenu'
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
import { queryClient } from '@/query/client'
import { keys } from '@/query/keys'
import { environmentQuery, roomMembersQuery } from '@/query/room'

const props = defineProps<{
  topicId: string
  projectId: string
  projectMembers: ProjectMemberRow[]
  me: string
  /** 我管不管这个频道（创建者或项目管理员）。 */
  canManage: boolean
  /** 不管频道也能加人：频道里的人都能加人，移出仍只归管理者。 */
  canInvite?: boolean
  /** 这是「综合」：项目里的人都在，名单上只有 AI 队友能加减。 */
  general?: boolean
}>()
const emit = defineEmits<{
  // 管这个频道的人叫什么：频道详情的「关于」里写它。名册是唯一记着它的地方。
  (e: 'manager', name: string | null): void
}>()

// 切回来过的房间先画上次那份名册，背后再重取（`query/room`）。骨架只在手上什么
// 都没有时出：有上次那份就照着它画，回来了原地换。
const roster = useQuery(
  computed(() => roomMembersQuery(props.topicId)),
  queryClient
)
const members = computed<TopicMemberRow[]>(() => roster.data.value?.data ?? [])
const loading = computed(() => roster.isPending.value && !roster.isError.value)
const busy = ref(false)
const error = ref('')
const open = ref(false)
const addHandle = ref<string | null>(null)

const loadError = computed(() =>
  roster.error.value ? roster.error.value.message || t('work.room.roster.loadFailed') : ''
)
// 改名册那一下没成，压过「名册没读到」：那是此刻手上这一下的结果。
const shownError = computed(() => error.value || loadError.value)

watch(
  members,
  (rows) => {
    const owner = rows.find((m) => m.role === 'owner' && !m.agent)
    emit('manager', owner ? memberName(owner) || owner.member_handle : null)
  },
  { immediate: true }
)

// 执行环境：一次读回新会话默认用哪个、每条会话各在哪个环境里（和任务页那一格同一份）。
// 它只喂两处：名册展开后的那一行，和页头那个「能访问整台机器」的标记。名册一展开就
// 读，所以进房间这一下没必要挤在首屏前 —— 推到浏览器空下来再问，标记晚一点补上。
const machinesWanted = ref(false)
const machinesRead = useQuery(
  computed(() => ({ ...environmentQuery(props.topicId), enabled: machinesWanted.value && !!props.topicId })),
  queryClient
)
const machines = computed<TopicEnvironment | null>(() => machinesRead.data.value ?? null)
const machinesError = computed(() =>
  machinesRead.error.value ? machinesRead.error.value.message || t('compute.env.loadFailed') : ''
)
function loadMachines() {
  if (!machinesWanted.value) machinesWanted.value = true
  else void queryClient.invalidateQueries({ queryKey: keys.roomEnvironment(props.topicId) })
}
let cancelIdleLoad: (() => void) | null = null
onMounted(() => {
  cancelIdleLoad = whenIdle(() => (machinesWanted.value = true))
  window.addEventListener('project-compute-updated', loadMachines)
})
onBeforeUnmount(() => {
  cancelIdleLoad?.()
  window.removeEventListener('project-compute-updated', loadMachines)
})
watch(open, (value) => {
  if (value) loadMachines()
})
// 一位 AI 队友在这里的会话：按它坐的那个名册位认（成员自己的 Claude Code 坐在主人
// 名下的位上，会话的 agent_handle 和名册上的 handle 不一定相同）。
function sessionsOf(handle: string): SessionEnvironment[] {
  return machines.value?.sessions.filter((s) => s.seat_handle === handle) ?? []
}
function saveSession(session: SessionEnvironment) {
  return (env: Environment) => setSessionEnvironment(props.topicId, session.session_id, env)
}
function saveDefault(env: Environment) {
  return setEnvironmentDefault(props.topicId, env)
}

// 一份名册：AI 队友就是上面的一行，不在人数外面再挂一个。列表本来就是这样渲染
// 的（`members` 全量），只有这颗按钮上的头像堆和人数把它挑出去单独摆，读起来像
// 「几个人，另外还有个它」。
// 「位」而不是「人」：同一句话要数得下一个 AI 队友。
const countLabel = computed(() => t('work.room.roster.count', { count: members.value.length }))

// Compact indicator: the first few human faces as a stack, capped so the
// stack never grows unbounded — extra people fold into a "+N" tile.
const MAX_FACES = 3
const stackFaces = computed(() => members.value.slice(0, MAX_FACES))
const overflow = computed(() => Math.max(0, members.value.length - MAX_FACES))

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

async function guard<T>(fn: () => Promise<T>): Promise<void> {
  busy.value = true
  error.value = ''
  try {
    await fn()
    // 写名册的那一下已经把名册标过期了（api/topicMembers），这里等新的一份回来再收起。
    await roster.refetch()
  } catch (e) {
    error.value = e instanceof Error ? e.message : t('work.room.roster.failed')
  } finally {
    busy.value = false
  }
}

async function onAdd() {
  const handle = addHandle.value
  if (!handle) return
  await guard(() => addTopicMember(props.topicId, handle))
  addHandle.value = null
}

async function onRemove(handle: string) {
  await guard(() => removeTopicMember(props.topicId, handle))
}
</script>

<template>
  <v-menu v-model="open" :close-on-content-click="false" location="bottom start" offset="6">
    <template #activator="{ props: act }">
      <button
        v-bind="act"
        type="button"
        class="members-mini tap-target"
        :class="{ 'members-mini--open': open }"
        :title="`${t('work.room.roster.title')} · ${countLabel}`"
      >
        <span class="members-mini__stack">
          <template v-for="(m, i) in stackFaces" :key="m.member_handle">
            <CheeseAvatar
              v-if="m.agent"
              class="members-mini__ai"
              :size="22"
              :name="memberName(m) || m.member_handle"
              :handle="m.member_handle"
              :style="{ zIndex: MAX_FACES - i }"
            />
            <UserAvatar
              v-else
              class="members-mini__face"
              :size="22"
              :name="memberName(m) || m.member_handle"
              :seed="m.member_handle"
              :avatar="m.avatar_id != null ? getAvatarUrl(m.avatar_id) : ''"
              :style="{ zIndex: MAX_FACES - i }"
            />
          </template>
          <span v-if="overflow" class="members-mini__face members-mini__face--more" :style="{ zIndex: 0 }"
            >+{{ overflow }}</span
          >
        </span>
      </button>
    </template>

    <div class="roster">
      <div class="roster__head">
        <span class="roster__title">{{ t('work.room.roster.title') }}</span>
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
        <li
          v-for="m in members"
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

          <!-- 管这个频道的人标一个「管理者」：这个人和项目管理员一起管这个频道。 -->
          <span v-if="m.role === 'owner' && !m.agent" class="roster__role">{{ t('work.room.roster.creator') }}</span>
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

      <div v-if="machines" class="roster__future" data-testid="future-machine">
        <span class="roster__machine-text">{{
          t('compute.env.defaultHere', {
            name: environmentName(
              machines.default ?? { kind: 'sandbox', spec: 'standard' },
              machines.devices,
              machines.specs
            ),
          })
        }}</span>
        <span v-if="machines.default_from === 'project'" class="roster__tag">{{
          t('compute.env.projectDefault')
        }}</span>
        <EnvironmentMenu
          :heading="t('compute.env.defaultHeading')"
          :hint="t('compute.env.defaultHint')"
          :current="machines.default"
          :devices="machines.devices"
          :specs="machines.specs"
          :save="saveDefault"
          @changed="loadMachines"
        />
      </div>
      <div v-else-if="machinesError" class="roster__hint">
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
      <div v-else class="roster__hint">{{ t('work.room.roster.readOnly') }}</div>
    </div>
  </v-menu>
</template>

<style scoped>
/* Compact roster indicator: an avatar stack + count, no full-width bar.
   Sits at the top-right of the topic/chat header row (fusion-design §3). */
/* 相对定位给 .tap-target：这一颗只有 28px 高，手机顶栏里手指要点得中。 */
.members-mini {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 28px;
  padding: 0 8px 0 5px;
  border: 1px solid transparent;
  border-radius: 999px;
  background: transparent;
  cursor: pointer;
  transition:
    background 0.12s ease,
    border-color 0.12s ease;
}
.members-mini:hover,
.members-mini--open {
  background: var(--fill);
  border-color: var(--line-2);
}
.members-mini__stack {
  display: inline-flex;
  align-items: center;
}
.members-mini__face {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  margin-left: -7px;
  font-size: 0.66rem;
  font-weight: 700;
  /* 这圈 #fff 是故意写死的：底色由 UserAvatar 按 seed（member_handle）用
     avatarColor() 算出来，两套主题下是同一个值，压在上面的字也得是同一个值。
     底色逐人不同 —— 一排脸共用一个颜色就等于没有脸，而这一排存在的意义正是彼此不同。 */
  color: #fff;
  border: 1.5px solid var(--surface);
  box-sizing: border-box;
  overflow: hidden;
}
.members-mini__face:first-child {
  margin-left: 0;
}
.members-mini__face--more {
  /* 不是一张脸，是「还有几个人」—— 用界面的填充色，别混进彩色头像里。 */
  background: var(--fill-2);
  color: var(--muted);
  font-size: 0.6rem;
}
/* AI 队友在头像堆里和在别处一个样子（CheeseAvatar）。叠在一起时和人的头像一样
   描一圈底色，前后两张脸才分得开：描边压在超椭圆的边上，约 1.5px。 */
.members-mini__ai {
  margin-left: -7px;
}
.members-mini__ai:first-child {
  margin-left: 0;
}
.members-mini__ai :deep(.cheese-avatar__tile) {
  stroke: var(--surface);
  stroke-width: 14px;
}

.roster {
  width: 360px;
  max-width: 88vw;
}
.roster__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  padding: 10px 14px;
  border-bottom: 1px solid var(--line-2);
}
.roster__title {
  font-weight: 600;
  font-size: 14px;
  color: var(--ink);
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
/* 名册底下：新会话默认用的环境。 */
.roster__future {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}
.roster__future {
  padding: 10px 14px;
  border-top: 1px solid var(--line-2);
}
.roster__machine-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.roster__tag {
  flex: none;
  padding: 0 4px;
  border-radius: var(--radius-sm);
  background: var(--fill);
  color: var(--muted);
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
  color: #fff; /* theme-invariant ground, see .members-mini__face */
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
.roster__role--btn {
  display: inline-flex;
  align-items: center;
  gap: 1px;
  padding: 2px 6px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-sm);
  background: var(--surface);
  cursor: pointer;
}
.roster__role--btn:hover:not(:disabled) {
  border-color: var(--line);
  color: var(--ink);
}
.roster__role--btn:disabled {
  opacity: 0.5;
  cursor: default;
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
