<script setup lang="ts">
// 话题成员名册 (群聊房间的地基, fusion-design §3): a topic is a group room, and
// this is who's in it. A compact header count ("3 人 + 芝士") opens a roster
// drawer showing every member with their role; an owner/admin can add project
// members, remove them, or change roles. 芝士 (the AI member) wears an Agent
// badge, mirroring the @-mention menu.
//
// 名册底下一行写这个话题在哪台工作电脑上跑。一个话题一个容器（2026-09-28，推翻
// 结论 60）：房间里的 AI 队友都在这一台上，所以不再每个队友各写一行。
import type { ProjectMemberRow, TopicComputeProfile, TopicMemberRow } from '../cx_types'
import type { MenuAction } from './common/menuAction'

import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { addTopicMember, getTopicComputeProfile, removeTopicMember, updateTopicMemberRole } from '../api'
import { useRowMenu } from '../composables/useRowMenu'
import { t } from '../i18n'
import { memberName } from '../lib/agentNames'
import { choiceKey, choiceName } from '../lib/computeConfig'
import { externalHandles } from '../lib/externalMembers'
import { whenIdle } from '../lib/idle'
import { cachedTopicPanel, fetchTopicMembers } from '../lib/topicPanelCache'
import { avatarColor, avatarInitial } from '../utils/avatar'
import { getAvatarUrl } from '../utils/materials'

import AdaptiveMenu from './common/AdaptiveMenu.vue'
import ExternalTag from './common/ExternalTag.vue'
import LoadingSkeleton from './common/LoadingSkeleton.vue'
import CheeseAvatar from './CheeseAvatar.vue'
import TopicComputePicker from './TopicComputePicker.vue'

import BaseButton from '@/components/base/BaseButton.vue'

const props = defineProps<{
  topicId: string
  projectId: string
  projectMembers: ProjectMemberRow[]
  me: string
}>()
const emit = defineEmits<{
  // 有 AI 队友能访问整台机器。这是权限，不是设置，名册合着的时候页头也要写着——
  // 挂它的地方据此常驻一个标记。null = 没有，或者还不知道。
  (e: 'machine-access', notice: string | null): void
}>()

// 切回来过的房间先画上次那份名册，背后再重取（lib/topicPanelCache.ts）。
const members = ref<TopicMemberRow[]>(cachedTopicPanel('members', props.topicId)?.data ?? [])
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const open = ref(false)
const addHandle = ref<string | null>(null)

const ROLES = ['owner', 'admin', 'member'] as const

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

// My role in THIS topic decides whether the management controls show at all.
const myRole = computed(() => members.value.find((m) => m.member_handle === props.me)?.role ?? null)
const canManage = computed(() => myRole.value === 'owner' || myRole.value === 'admin')
const ownerCount = computed(() => members.value.filter((m) => m.role === 'owner').length)

// 右键一位成员：行里那个角色菜单和移出按钮，收成一份弹在鼠标那一点上。最后一个拥有者
// 不能被降级或移出，那几项和行里一样点不动。
const rowMenu = useRowMenu<string>()
function memberActions(m: TopicMemberRow): MenuAction[] {
  const lastOwner = m.role === 'owner' && ownerCount.value <= 1
  const roles: MenuAction[] = m.agent
    ? []
    : ROLES.filter((r) => r !== m.role).map((r) => ({
        key: `role.${r}`,
        label: t('work.room.roster.setRole', { role: roleLabel(r) }),
        icon: 'mdi-account-key-outline',
        disabled: busy.value || lastOwner,
        onSelect: () => void onSetRole(m.member_handle, r),
      }))
  return [
    ...roles,
    {
      key: 'remove',
      label: t('work.room.roster.remove'),
      icon: 'mdi-account-remove-outline',
      danger: true,
      disabled: busy.value || lastOwner,
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
  return props.projectMembers
    .filter((m) => !inRoom.has(m.user_handle) && m.active !== false)
    .map((m) => ({
      title: memberName(m) || m.user_handle,
      value: m.user_handle,
      agent: !!m.agent,
      external: externals.value.has(m.user_handle),
      face: m.avatar_id != null && !broken.value.has(m.user_handle) ? getAvatarUrl(m.avatar_id) : null,
    }))
})

// 头像：本人挑过就画本人的，没挑过画按 handle 哈希出的彩色首字母。种子用
// handle 而不是昵称 —— 改个昵称不该换一张脸，而重名的两个人得是两种颜色。
const broken = ref<Set<string>>(new Set())
function faceSrc(m: TopicMemberRow): string | null {
  if (m.avatar_id == null || broken.value.has(m.member_handle)) return null
  return getAvatarUrl(m.avatar_id)
}
function onFaceError(handle: string): void {
  if (broken.value.has(handle)) return
  broken.value = new Set(broken.value).add(handle)
}
function faceColor(m: TopicMemberRow): string {
  return avatarColor(m.member_handle)
}
function initial(name: string): string {
  return avatarInitial(name)
}

function roleLabel(role: string): string {
  return ROLES.includes(role as (typeof ROLES)[number]) ? t(`work.room.roster.role.${role}`) : role
}

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
  await guard(() => addTopicMember(props.topicId, handle, 'member'))
  addHandle.value = null
}

async function onRemove(handle: string) {
  await guard(() => removeTopicMember(props.topicId, handle))
}

async function onSetRole(handle: string, role: string) {
  await guard(() => updateTopicMemberRole(props.topicId, handle, role))
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
          <template v-for="(m, i) in stackFaces" :key="m.id">
            <CheeseAvatar
              v-if="m.agent"
              class="members-mini__ai"
              :size="22"
              :name="memberName(m) || m.member_handle"
              :handle="m.member_handle"
              :style="{ zIndex: MAX_FACES - i }"
            />
            <img
              v-else-if="faceSrc(m)"
              decoding="async"
              class="members-mini__face members-mini__face--photo"
              :src="faceSrc(m)!"
              :alt="memberName(m) || m.member_handle"
              :style="{ zIndex: MAX_FACES - i }"
              @error="onFaceError(m.member_handle)"
            />
            <span v-else class="members-mini__face" :style="{ zIndex: MAX_FACES - i, backgroundColor: faceColor(m) }">{{
              initial(memberName(m) || m.member_handle)
            }}</span>
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
      <ul v-else class="roster__list">
        <li
          v-for="m in members"
          :key="m.id"
          class="roster__item"
          @contextmenu="canManage && rowMenu.open(m.member_handle, $event)"
        >
          <AdaptiveMenu
            v-if="canManage"
            v-bind="rowMenu.bind(m.member_handle)"
            :actions="memberActions(m)"
            :title="memberName(m) || m.member_handle"
          >
            <template #activator />
          </AdaptiveMenu>
          <CheeseAvatar v-if="m.agent" :size="26" :name="memberName(m) || m.member_handle" :handle="m.member_handle" />
          <img
            v-else-if="faceSrc(m)"
            decoding="async"
            class="roster__avatar roster__avatar--photo"
            :src="faceSrc(m)!"
            :alt="memberName(m) || m.member_handle"
            @error="onFaceError(m.member_handle)"
          />
          <span v-else class="roster__avatar" :style="{ backgroundColor: faceColor(m) }">{{
            initial(memberName(m) || m.member_handle)
          }}</span>
          <span class="roster__who">
            <span class="roster__name">{{ memberName(m) || m.member_handle }}</span>
            <span class="roster__handle">@{{ m.member_handle }}</span>
          </span>
          <span v-if="m.agent" class="roster__badge">{{ t('work.room.roster.agentBadge') }}</span>
          <ExternalTag v-else-if="externals.has(m.member_handle)" />

          <!-- Owner/admin: change role via a small menu; else a static chip.
               队友没有角色菜单——它在房间里的身份是「AI 队友」那个标——但和人一样
               能被移出。 -->
          <template v-if="canManage">
            <v-menu v-if="!m.agent" location="bottom end">
              <template #activator="{ props: rp }">
                <button v-bind="rp" type="button" class="roster__role roster__role--btn" :disabled="busy">
                  {{ roleLabel(m.role) }}
                  <v-icon size="12">mdi-chevron-down</v-icon>
                </button>
              </template>
              <v-list density="compact">
                <v-list-item
                  v-for="r in ROLES"
                  :key="r"
                  :active="r === m.role"
                  :disabled="m.role === 'owner' && r !== 'owner' && ownerCount <= 1"
                  @click="onSetRole(m.member_handle, r)"
                >
                  <v-list-item-title class="text-body-2">
                    {{ roleLabel(r) }}
                  </v-list-item-title>
                </v-list-item>
              </v-list>
            </v-menu>
            <button
              type="button"
              class="roster__remove"
              :disabled="busy || (m.role === 'owner' && ownerCount <= 1)"
              :title="t('work.room.roster.remove')"
              @click="onRemove(m.member_handle)"
            >
              <v-icon size="15">mdi-close</v-icon>
            </button>
          </template>
          <!-- 芝士不写角色：它在房间里的身份是 Agent 那个标，「成员」对它没有意义。 -->
          <span v-else-if="!m.agent" class="roster__role">{{ roleLabel(m.role) }}</span>
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
        <TopicComputePicker :topic-id="topicId" :project-id="projectId" :profile="machines" @changed="loadMachines" />
      </div>
      <div v-else-if="machinesError" class="roster__hint">
        {{ t('work.roomMachine.loadFailed') }}
        <button type="button" class="roster__retry" @click="loadMachines">{{ t('work.roomMachine.retry') }}</button>
      </div>

      <!-- Add a project member (owner/admin only). -->
      <div v-if="canManage" class="roster__add">
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
                <img
                  v-else-if="item.raw.face"
                  decoding="async"
                  class="roster__avatar roster__avatar--photo"
                  :src="item.raw.face"
                  :alt="item.raw.title"
                  @error="onFaceError(item.raw.value)"
                />
                <span v-else class="roster__avatar" :style="{ backgroundColor: avatarColor(item.raw.value) }">{{
                  initial(item.raw.title)
                }}</span>
              </template>
              <span class="roster__who">
                <span class="roster__name">{{ item.raw.title }}</span>
                <span class="roster__handle">@{{ item.raw.value }}</span>
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
  /* Theme-invariant pair, kept literal on purpose (same call as the default
     avatar in LeftAppRail): the disc under it is the #rrggbb avatarColor()
     computes at a fixed PERCEPTUAL lightness, one value in both themes, so the
     initial on it must be one value too.
     The disc itself is set inline per member — a single shared slate made
     every face in the stack identical, which is the one thing a row of faces
     exists not to be. */
  color: #fff;
  border: 1.5px solid var(--surface);
  box-sizing: border-box;
  overflow: hidden;
}
.members-mini__face--photo {
  object-fit: cover;
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
.roster__empty,
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
.roster__avatar--photo {
  object-fit: cover;
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
