<script setup lang="ts">
// 话题成员名册 (群聊房间的地基, fusion-design §3): a topic is a group room, and
// this is who's in it. A compact header count ("3 人 + 芝士") opens a roster
// drawer showing every member with their role; an owner/admin can add project
// members, remove them, or change roles. 芝士 (the AI member) wears an Agent
// badge, mirroring the @-mention menu.
import { computed, ref, watch } from 'vue'
import {
  addTopicMember,
  listTopicMembers,
  removeTopicMember,
  updateTopicMemberRole,
} from '../api'
import type { ProjectMemberRow, TopicMemberRow } from '../types'

const props = defineProps<{
  topicId: string
  projectMembers: ProjectMemberRow[]
  me: string
}>()

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
const hasAgent = computed(() => members.value.some((m) => m.agent))
const countLabel = computed(() => {
  const n = humans.value.length
  return hasAgent.value ? `${n} 人 + 芝士` : `${n} 人`
})

// My role in THIS topic decides whether the management controls show at all.
const myRole = computed(
  () => members.value.find((m) => m.member_handle === props.me)?.role ?? null,
)
const canManage = computed(
  () => myRole.value === 'owner' || myRole.value === 'admin',
)
const ownerCount = computed(
  () => members.value.filter((m) => m.role === 'owner').length,
)

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

function initial(name: string): string {
  return (name || '?').trim().charAt(0).toUpperCase()
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
</script>

<template>
  <v-menu
    v-model="open"
    :close-on-content-click="false"
    location="bottom start"
    offset="6"
  >
    <template #activator="{ props: act }">
      <button
        v-bind="act"
        type="button"
        class="members-pill"
        :class="{ 'members-pill--open': open }"
        title="话题成员"
      >
        <v-icon size="15" class="members-pill__icon">mdi-account-group</v-icon>
        <span>{{ countLabel }}</span>
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
          <span
            class="roster__avatar"
            :class="{ 'roster__avatar--agent': m.agent }"
          >
            <template v-if="m.agent">芝</template>
            <template v-else>{{ initial(m.name || m.member_handle) }}</template>
          </span>
          <span class="roster__who">
            <span class="roster__name">{{ m.name || m.member_handle }}</span>
            <span class="roster__handle">@{{ m.member_handle }}</span>
          </span>
          <span v-if="m.agent" class="roster__badge">Agent</span>

          <!-- Owner/admin: change role via a small menu; else a static chip. -->
          <template v-if="canManage && !m.agent">
            <v-menu location="bottom end">
              <template #activator="{ props: rp }">
                <button
                  v-bind="rp"
                  type="button"
                  class="roster__role roster__role--btn"
                  :disabled="busy"
                >
                  {{ roleLabel(m.role) }}
                  <v-icon size="12">mdi-chevron-down</v-icon>
                </button>
              </template>
              <v-list density="compact">
                <v-list-item
                  v-for="r in ROLES"
                  :key="r"
                  :active="r === m.role"
                  :disabled="
                    m.role === 'owner' && r !== 'owner' && ownerCount <= 1
                  "
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
          <span v-else class="roster__role">{{ roleLabel(m.role) }}</span>
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
          placeholder="加成员…"
          no-data-text="项目成员都在话题里了"
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
      <div v-else class="roster__hint">只有 owner / admin 能改成员</div>
    </div>
  </v-menu>
</template>

<style scoped>
.members-pill {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 26px;
  padding: 0 10px;
  border: 1px solid var(--line-2, #e0e0e0);
  border-radius: 13px;
  background: var(--surface, #fff);
  color: var(--muted, #6b6b6b);
  font-size: 0.78rem;
  font-weight: 500;
  cursor: pointer;
}
.members-pill:hover,
.members-pill--open {
  border-color: var(--line, #ccc);
  color: var(--ink, #222);
}
.members-pill__icon {
  color: var(--muted, #8a94a3);
}

.roster {
  width: 320px;
  max-width: 88vw;
  background: var(--surface, #fff);
  border: 1px solid var(--line-2, #e0e0e0);
  border-radius: 10px;
  overflow: hidden;
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.1);
}
.roster__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  padding: 10px 14px;
  border-bottom: 1px solid var(--line-2, #eee);
}
.roster__title {
  font-weight: 600;
  font-size: 0.9rem;
  color: var(--ink, #222);
}
.roster__count {
  font-size: 0.75rem;
  color: var(--muted, #999);
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
  color: var(--muted, #999);
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
  background: var(--fill, #f6f7f8);
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
  color: #fff;
  background: #8a94a3;
  flex: none;
}
.roster__avatar--agent {
  background: var(--ink, #222);
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
  color: var(--ink, #222);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.roster__handle {
  font-size: 0.72rem;
  color: var(--muted, #999);
}
.roster__badge {
  font-size: 0.62rem;
  font-weight: 600;
  padding: 1px 5px;
  border-radius: 4px;
  color: rgb(var(--v-theme-primary));
  background: rgba(var(--v-theme-primary), 0.12);
}
.roster__role {
  font-size: 0.72rem;
  color: var(--muted, #888);
  flex: none;
}
.roster__role--btn {
  display: inline-flex;
  align-items: center;
  gap: 1px;
  padding: 2px 6px;
  border: 1px solid var(--line-2, #e0e0e0);
  border-radius: 6px;
  background: var(--surface, #fff);
  cursor: pointer;
}
.roster__role--btn:hover:not(:disabled) {
  border-color: var(--line, #ccc);
  color: var(--ink, #222);
}
.roster__role--btn:disabled {
  opacity: 0.5;
  cursor: default;
}
.roster__remove {
  display: inline-flex;
  align-items: center;
  color: var(--muted, #aaa);
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
  border-top: 1px solid var(--line-2, #eee);
}
.roster__select {
  flex: 1 1 auto;
}
</style>
