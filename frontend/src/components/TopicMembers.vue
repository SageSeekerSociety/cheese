<script setup lang="ts">
// 频道里有谁：页头一小串头像，点开是名单。频道里的人都能从项目成员里加人、请 AI
// 队友，移人只归频道管理者和项目管理员；「综合」是项目里的所有人，那里只管
// AI 队友。AI 队友带「AI 队友」标，和 @ 菜单里一样。
//
// 名册底下一行写这个话题在哪台工作电脑上跑。一个话题一个容器（2026-09-28，推翻
// 结论 60）：房间里的 AI 队友都在这一台上，所以不再每个队友各写一行。
import type { ProjectMemberRow, TopicMemberRow } from '../cx_types'
import type { TopicComputeProfile } from '../types/compute'
import type { MenuAction } from './common/menuAction'

import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { addTopicMember, getTopicComputeProfile, removeTopicMember } from '../api'
import { useRowMenu } from '../composables/useRowMenu'
import { t } from '../i18n'
import { memberName } from '../lib/agentNames'
import { choiceKey, choiceName } from '../lib/computeConfig'
import { externalHandles } from '../lib/externalMembers'
import { whenIdle } from '../lib/idle'
import { cachedTopicPanel, fetchTopicMembers } from '../lib/topicPanelCache'
import { getAvatarUrl } from '../utils/materials'

import AdaptiveMenu from './common/AdaptiveMenu.vue'
import ExternalTag from './common/ExternalTag.vue'
import LoadingSkeleton from './common/LoadingSkeleton.vue'
import UserAvatar from './common/UserAvatar.vue'
import CheeseAvatar from './CheeseAvatar.vue'
import TopicComputePicker from './TopicComputePicker.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'

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
  // 有 AI 队友能访问整台机器。这是权限，不是设置，名册合着的时候页头也要写着——
  // 挂它的地方据此常驻一个标记。null = 没有，或者还不知道。
  (e: 'machine-access', notice: string | null): void
  // 管这个频道的人叫什么：频道详情的「关于」里写它。名册是唯一记着它的地方。
  (e: 'manager', name: string | null): void
}>()

// 切回来过的房间先画上次那份名册，背后再重取（lib/topicPanelCache.ts）。
const members = ref<TopicMemberRow[]>(cachedTopicPanel('members', props.topicId)?.data ?? [])
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const open = ref(false)
const addHandle = ref<string | null>(null)

async function load() {
  if (!props.topicId) return
  loading.value = true
  error.value = ''
  try {
    const payload = await fetchTopicMembers(props.topicId)
    members.value = payload.data
  } catch (e) {
    error.value = e instanceof Error ? e.message : t('work.room.roster.loadFailed')
  } finally {
    loading.value = false
  }
}

void load()

watch(
  members,
  (rows) => {
    const owner = rows.find((m) => m.role === 'owner' && !m.agent)
    emit('manager', owner ? memberName(owner) || owner.member_handle : null)
  },
  { immediate: true }
)

// 工作电脑：一次读回房间这一项和每个会话在哪台机器上。
const machines = ref<TopicComputeProfile | null>(null)
const machinesError = ref('')
async function loadMachines() {
  if (!props.topicId) return
  machinesError.value = ''
  try {
    machines.value = await getTopicComputeProfile(props.topicId)
  } catch (e) {
    machinesError.value = e instanceof Error ? e.message : t('work.roomMachine.loadFailed')
  }
}
let cancelIdleLoad: (() => void) | null = null
onMounted(() => {
  // 工作电脑这一项只喂两处：名册展开后的那一行，和页头那个「能访问整台机器」的标记。
  // 名册一展开（下面的 watch）会立刻读一次，所以进房间这一下没必要挤在首屏前 —— 推到
  // 浏览器空下来再问，标记晚一点补上，读到的仍是同一份。
  const tid = props.topicId
  cancelIdleLoad = whenIdle(() => {
    if (props.topicId === tid) void loadMachines()
  })
  window.addEventListener('project-compute-updated', loadMachines)
})
onBeforeUnmount(() => {
  cancelIdleLoad?.()
  window.removeEventListener('project-compute-updated', loadMachines)
})
watch(open, (value) => {
  if (value) void loadMachines()
})
watch(
  () => (machines.value?.visibility.machine_access ? t('work.roomMachine.wholeMachineNotice') : null),
  (notice) => emit('machine-access', notice),
  { immediate: true }
)

// 「项目默认」只挂在还没开工的选择上：只有这时它才真的跟着项目走。
const roomChoiceIsProjectDefault = computed(
  () => !!machines.value && choiceKey(machines.value.choice) === choiceKey(machines.value.project_default)
)

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
      .filter((m) => !inRoom.has(m.user_handle) && m.active !== false)
      // 「综合」里本来就有项目里的每个人，只剩 AI 队友可请。
      .filter((m) => !props.general || m.agent)
      .map((m) => ({
        title: memberName(m) || m.user_handle,
        value: m.user_handle,
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
    await load()
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

      <div v-if="error" class="roster__error">{{ error }}</div>

      <LoadingSkeleton v-if="loading" variant="roster" />
      <BaseEmptyState
        v-else-if="!members.length && !error"
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
            <span class="roster__handle">{{ m.member_handle }}</span>
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
        </li>
      </ul>

      <div v-if="machines" class="roster__future" data-testid="future-machine">
        <span class="roster__machine-text">{{
          t('work.roomMachine.here', { name: choiceName(machines.choice) })
        }}</span>
        <span v-if="roomChoiceIsProjectDefault" class="roster__tag">{{ t('work.roomMachine.projectDefault') }}</span>
        <span
          v-if="machines.visibility.machine_access"
          class="roster__notice"
          :title="t('work.roomMachine.wholeMachineNotice')"
        >
          <span class="status-dot status-dot--warn" />{{ t('work.roomMachine.wholeMachine') }}
        </span>
        <TopicComputePicker :topic-id="topicId" :profile="machines" @changed="loadMachines" />
      </div>
      <div v-else-if="machinesError" class="roster__hint">
        {{ t('work.roomMachine.loadFailed') }}
        <button type="button" class="roster__retry" @click="loadMachines">{{ t('work.roomMachine.retry') }}</button>
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
                <span class="roster__handle">{{ item.raw.value }}</span>
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
/* 房间那一行：这个话题在哪台工作电脑上跑。 */
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
.roster__notice {
  display: inline-flex;
  flex: none;
  align-items: center;
  gap: 4px;
  color: var(--text);
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
