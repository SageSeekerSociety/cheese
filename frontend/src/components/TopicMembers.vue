<script setup lang="ts">
// 话题成员名册 (群聊房间的地基, fusion-design §3): a topic is a group room, and
// this is who's in it. A compact header count ("3 人 + 芝士") opens a roster
// drawer showing every member with their role; an owner/admin can add project
// members, remove them, or change roles. 芝士 (the AI member) wears an Agent
// badge, mirroring the @-mention menu.
import type { ProjectMemberRow, TopicMemberRow } from '../cx_types'

import { computed, ref, watch } from 'vue'

import { addTopicMember, listTopicMembers, removeTopicMember, updateTopicMemberRole } from '../api'
import { avatarColor, avatarInitial } from '../utils/avatar'
import { getAvatarUrl } from '../utils/materials'

import TopicAgentPicker from './TopicAgentPicker.vue'

const props = defineProps<{
  topicId: string
  /** 换 AI 队友要从这个项目的队友里挑，见 TopicAgentPicker。 */
  projectId: string
  projectMembers: ProjectMemberRow[]
  me: string
}>()

// 换完 AI 队友要往上说一声：对话栏那边每一条它说的话也挂着它的名字，而那一栏
// 够不着这个组件。
const emit = defineEmits<{ 'agent-swapped': [] }>()

const members = ref<TopicMemberRow[]>([])
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
  const forTopic = props.topicId
  try {
    const payload = await listTopicMembers(forTopic)
    if (props.topicId === forTopic) members.value = payload.data
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载成员失败'
  } finally {
    loading.value = false
  }
}

watch(() => props.topicId, load, { immediate: true })

// 群聊感: humans + whether 芝士 is in the room, shown as "N 人 + 芝士".
const humans = computed(() => members.value.filter((m) => !m.agent))
const agentRow = computed(() => members.value.find((m) => m.agent) ?? null)
// 这个房间现在交给的是哪个 AI 队友 —— 名册那一行说了算（后端把它解析成当前
// 队友的名字）。**不要**在界面上写死「芝士」：一个项目可以有好几个队友，写死
// 的字换完队友不会变，看起来就像换人没生效。
const agentName = computed(() => agentRow.value?.name || agentRow.value?.member_handle || '')
const countLabel = computed(() => {
  const n = humans.value.length
  return agentRow.value ? `${n} 人 + ${agentName.value}` : `${n} 人`
})

// Compact indicator: the first few human faces as a stack, capped so the
// stack never grows unbounded — extra people fold into a "+N" tile.
const MAX_FACES = 3
const stackFaces = computed(() => humans.value.slice(0, MAX_FACES))
const overflow = computed(() => Math.max(0, humans.value.length - MAX_FACES))

// My role in THIS topic decides whether the management controls show at all.
const myRole = computed(() => members.value.find((m) => m.member_handle === props.me)?.role ?? null)
const canManage = computed(() => myRole.value === 'owner' || myRole.value === 'admin')
const ownerCount = computed(() => members.value.filter((m) => m.role === 'owner').length)

// Project members not already in the room — the "add member" dropdown.
const addable = computed(() => {
  const inRoom = new Set(members.value.map((m) => m.member_handle))
  return props.projectMembers
    .filter((m) => !inRoom.has(m.user_handle))
    .map((m) => ({
      title: m.name || m.user_handle,
      subtitle: `@${m.user_handle}`,
      value: m.user_handle,
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
  return { owner: '拥有者', admin: '管理员', member: '成员' }[role] ?? role
}

async function guard<T>(fn: () => Promise<T>): Promise<void> {
  busy.value = true
  error.value = ''
  try {
    await fn()
    await load()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '操作失败'
  } finally {
    busy.value = false
  }
}

async function onAdd() {
  const handle = addHandle.value
  if (!handle) return
  await guard(() => addTopicMember(props.topicId, handle, 'member', props.me))
  addHandle.value = null
}

async function onRemove(handle: string) {
  await guard(() => removeTopicMember(props.topicId, handle, props.me))
}

async function onSetRole(handle: string, role: string) {
  await guard(() => updateTopicMemberRole(props.topicId, handle, role, props.me))
}

// 换完队友这份名册就过期了：芝士那一行的名字来自它。不重新拉一次的话，屏幕上
// 留着的是上一个队友的名字，和「换人没生效」长得一模一样。
function onAgentSwapped() {
  void load()
  emit('agent-swapped')
}
</script>

<template>
  <v-menu v-model="open" :close-on-content-click="false" location="bottom start" offset="6">
    <template #activator="{ props: act }">
      <button
        v-bind="act"
        type="button"
        class="members-mini"
        :class="{ 'members-mini--open': open }"
        :title="`话题成员 · ${countLabel}`"
      >
        <span class="members-mini__stack">
          <template v-for="(m, i) in stackFaces" :key="m.id">
            <img
              v-if="faceSrc(m)"
              class="members-mini__face members-mini__face--photo"
              :src="faceSrc(m)!"
              :alt="m.name || m.member_handle"
              :style="{ zIndex: MAX_FACES - i }"
              @error="onFaceError(m.member_handle)"
            />
            <span v-else class="members-mini__face" :style="{ zIndex: MAX_FACES - i, backgroundColor: faceColor(m) }">{{
              initial(m.name || m.member_handle)
            }}</span>
          </template>
          <span v-if="overflow" class="members-mini__face members-mini__face--more" :style="{ zIndex: 0 }"
            >+{{ overflow }}</span
          >
          <span
            v-if="agentRow"
            class="members-mini__face members-mini__face--agent"
            :style="{ zIndex: MAX_FACES + 1 }"
            :title="`${agentName}在这个话题里`"
            >{{ initial(agentName) }}</span
          >
        </span>
        <span class="members-mini__count">{{ humans.length }}</span>
      </button>
    </template>

    <div class="roster">
      <div class="roster__head">
        <span class="roster__title">话题成员</span>
        <span class="roster__count">{{ countLabel }}</span>
      </div>

      <div v-if="error" class="roster__error">{{ error }}</div>

      <div v-if="loading" class="roster__empty">加载中…</div>
      <ul v-else class="roster__list">
        <li v-for="m in members" :key="m.id" class="roster__item">
          <span v-if="m.agent" class="roster__avatar roster__avatar--agent">{{ initial(agentName) }}</span>
          <img
            v-else-if="faceSrc(m)"
            class="roster__avatar roster__avatar--photo"
            :src="faceSrc(m)!"
            :alt="m.name || m.member_handle"
            @error="onFaceError(m.member_handle)"
          />
          <span v-else class="roster__avatar" :style="{ backgroundColor: faceColor(m) }">{{
            initial(m.name || m.member_handle)
          }}</span>
          <span class="roster__who">
            <span class="roster__name">{{ m.name || m.member_handle }}</span>
            <span class="roster__handle">@{{ m.member_handle }}</span>
          </span>
          <span v-if="m.agent" class="roster__badge">AI 队友</span>

          <!-- 芝士那一行：换一个 AI 队友。和换人的角色同一个位置、同一个样子。 -->
          <TopicAgentPicker
            v-if="m.agent && canManage"
            :topic-id="topicId"
            :project-id="projectId"
            @swapped="onAgentSwapped"
          />

          <!-- Owner/admin: change role via a small menu; else a static chip. -->
          <template v-if="canManage && !m.agent">
            <v-menu location="bottom end">
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
              title="移出话题"
              @click="onRemove(m.member_handle)"
            >
              <v-icon size="15">mdi-close</v-icon>
            </button>
          </template>
          <!-- 芝士不写角色：它在房间里的身份是 Agent 那个标，「成员」对它没有意义，
               和左边的「换」并排更像是两个能点的东西。 -->
          <span v-else-if="!m.agent" class="roster__role">{{ roleLabel(m.role) }}</span>
        </li>
      </ul>

      <!-- Add a project member (owner/admin only). -->
      <div v-if="canManage" class="roster__add">
        <v-select
          v-model="addHandle"
          :items="addable"
          density="compact"
          variant="outlined"
          hide-details
          placeholder="添加成员…"
          no-data-text="项目成员都已在话题中"
          class="roster__select"
        />
        <v-btn
          size="small"
          variant="flat"
          color="primary"
          :disabled="!addHandle || busy"
          :loading="busy"
          @click="onAdd"
        >
          加入
        </v-btn>
      </div>
      <div v-else class="roster__hint">只有拥有者和管理员能修改成员</div>
    </div>
  </v-menu>
</template>

<style scoped>
/* Compact roster indicator: an avatar stack + count, no full-width bar.
   Sits at the top-right of the topic/chat header row (fusion-design §3). */
.members-mini {
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
.members-mini__face--agent {
  /* on-primary, not the inherited #fff: dark lightens the amber to #FFA733,
     where white ink measures 1.9:1. */
  color: rgb(var(--v-theme-on-primary));
  background: var(--accent);
  font-size: 0.6rem;
}
.members-mini__count {
  font-size: 0.78rem;
  font-weight: 600;
  color: var(--muted);
  line-height: 1;
}
.members-mini:hover .members-mini__count,
.members-mini--open .members-mini__count {
  color: var(--ink);
}

.roster {
  width: 320px;
  max-width: 88vw;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  overflow: hidden;
  box-shadow: var(--shadow-2);
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
  font-size: 0.9rem;
  color: var(--ink);
}
.roster__count {
  font-size: 0.75rem;
  color: var(--muted);
}
.roster__error {
  padding: 8px 14px;
  font-size: 0.78rem;
  color: rgb(var(--v-theme-error, 211, 47, 47));
  background: rgba(var(--v-theme-error, 211, 47, 47), 0.08);
}
.roster__empty,
.roster__hint {
  padding: 12px 14px;
  font-size: 0.78rem;
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
  align-items: center;
  gap: 9px;
  padding: 6px 14px;
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
  border-radius: 8px;
  font-size: 0.72rem;
  font-weight: 700;
  color: #fff; /* theme-invariant ground, see .members-mini__face */
  flex: none;
  overflow: hidden;
}
.roster__avatar--photo {
  object-fit: cover;
}
.roster__avatar--agent {
  /* --ink inverts with the theme, so the ink on it has to invert too: --surface
     is #fff in light (unchanged) and #1B1D20 in dark. The inherited #fff would
     be white-on-near-white there. */
  color: var(--surface);
  background: var(--ink);
  font-size: 0.62rem;
}
.roster__who {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1 1 auto;
}
.roster__name {
  font-size: 0.84rem;
  font-weight: 500;
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.roster__handle {
  font-size: 0.72rem;
  color: var(--muted);
}
.roster__badge {
  font-size: 0.62rem;
  font-weight: 600;
  padding: 1px 5px;
  border-radius: var(--radius-sm);
  color: rgb(var(--v-theme-primary));
  background: rgba(var(--v-theme-primary), 0.12);
}
.roster__role {
  font-size: 0.72rem;
  color: var(--muted);
  flex: none;
}
.roster__role--btn {
  display: inline-flex;
  align-items: center;
  gap: 1px;
  padding: 2px 6px;
  border: 1px solid var(--line-2);
  border-radius: 6px;
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
