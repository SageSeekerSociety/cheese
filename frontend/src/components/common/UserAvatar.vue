<!-- The one avatar control for the whole site. Give it a `user-id` (and optionally an
     `avatar-id`/`nickname` for instant paint) and it becomes agent-aware automatically:
     if that user is an agent it shows a robot badge, the live cheeselet status (working
     ring + elapsed·tokens), and clicking opens its 现场 (the app-global viewer).
     Back-compat: the legacy `:avatar="<url>"` string prop still renders a plain avatar.
     Chat surfaces may instead pass a thread `:member` (or legacy `:user`) that already
     carries live status, avoiding a presence lookup. -->
<template>
  <div
    class="ua"
    :class="{ 'ua--agent': isAgent, 'ua--working': working, 'ua--clickable': canOpenScene }"
    :title="titleText"
    :style="{ width: `${px}px`, height: `${px}px` }"
    @click.stop="onClick"
  >
    <v-avatar :size="px" :color="isAgent ? 'deep-purple-lighten-1' : 'blue-grey-lighten-1'">
      <v-img :src="avatarUrl" :alt="nickname" />
    </v-avatar>
    <v-icon v-if="isAgent" class="ua__badge" :size="Math.round(px * 0.42)" icon="mdi-robot-happy" />
    <span v-if="showMeta && working && statusBadge" class="ua__meta">{{ statusBadge }}</span>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, watch } from 'vue'

import type { UserSummary } from '@/network/api/workspace'
import { type Member, isAgentWorking } from '@/network/api/threads'
import { useAgentPresenceStore } from '@/stores/agentPresence'
import { useAgentSceneStore } from '@/stores/agentScene'
import { getAvatarUrl } from '@/utils/materials'

const props = withDefaults(
  defineProps<{
    // Preferred: a user id → agent-awareness resolved from the presence store.
    userId?: number
    avatarId?: number | null
    nickname?: string
    // Chat surfaces pass a live member/user directly (no presence lookup needed).
    member?: Member
    user?: UserSummary
    // Legacy: a pre-built avatar URL string.
    avatar?: string
    size?: number | string
    clickable?: boolean
    // Show the elapsed·tokens badge under a working agent. Off for cramped surfaces
    // (e.g. the 已阅 popover rows) where it would overflow / clip.
    showMeta?: boolean
  }>(),
  { size: 48, clickable: true, showMeta: true },
)

const presence = useAgentPresenceStore()
const scene = useAgentSceneStore()

const px = computed(() => (typeof props.size === 'number' ? props.size : parseInt(props.size) || 48))

// The effective user id — from any of the input shapes. We ALWAYS track presence for it
// (humans resolve to non-agent once and stop polling; agents keep their live sid/status
// fresh), so EVERY avatar behaves identically: an agent anywhere is badge-marked and its
// 现场 opens on click, even when the caller passed a member row with a stale/missing sid.
const effId = computed(() => props.userId ?? props.member?.user_id ?? props.user?.id ?? 0)
watch(
  effId,
  (id, prev) => {
    if (prev) presence.untrack(prev)
    if (id) presence.track(id)
  },
  { immediate: true },
)
onBeforeUnmount(() => {
  if (effId.value) presence.untrack(effId.value)
})

// Live presence row for this id (used as the fallback source for agent-ness + sid + status).
const pres = computed<Member | undefined>(() => (effId.value ? presence.get(effId.value) : undefined))

const isAgent = computed(() => props.member?.is_agent ?? props.user?.isAgent ?? pres.value?.is_agent ?? false)
const avatarId = computed<number | null>(
  () => props.avatarId ?? props.member?.avatar_id ?? props.user?.avatarId ?? pres.value?.avatar_id ?? null,
)
const nickname = computed(
  () =>
    props.nickname ||
    props.member?.nickname ||
    props.user?.nickname ||
    props.user?.username ||
    pres.value?.nickname ||
    '',
)
const avatarUrl = computed(() =>
  props.avatar !== undefined ? props.avatar : getAvatarUrl(avatarId.value ?? undefined),
)

const status = computed(
  () => props.member?.agent_status ?? props.user?.agentStatus ?? pres.value?.agent_status ?? null,
)
const working = computed(() => isAgent.value && isAgentWorking(status.value))
const elapsed = computed(() => props.member?.elapsed ?? pres.value?.elapsed ?? null)
const tokens = computed(() => props.member?.tokens ?? pres.value?.tokens ?? null)
const statusBadge = computed(() => [elapsed.value, tokens.value].filter(Boolean).join(' · '))

// sid/device_id: prefer the caller's inline value, else the live presence row.
const sid = computed(() => props.member?.sid ?? pres.value?.sid ?? null)
const deviceId = computed(() => props.member?.device_id ?? pres.value?.device_id ?? null)
const agentUserId = computed(() => effId.value)
const canOpenScene = computed(() => props.clickable && isAgent.value && !!sid.value && !!deviceId.value)

const titleText = computed(() => {
  if (!isAgent.value) return nickname.value
  if (canOpenScene.value) return `${nickname.value} · 点击查看现场`
  return `${nickname.value} · 智能体`
})

function onClick(): void {
  if (!canOpenScene.value) return
  scene.open({
    sid: sid.value as string,
    device_id: deviceId.value as string,
    agent_user_id: agentUserId.value,
    nickname: nickname.value || undefined,
  })
}
</script>

<style scoped>
.ua {
  position: relative;
  display: inline-flex;
  border-radius: 50%;
  flex: none;
}
.ua--clickable {
  cursor: pointer;
}
.ua__badge {
  position: absolute;
  right: -2px;
  bottom: -2px;
  color: rgb(var(--v-theme-primary));
  background: rgb(var(--v-theme-surface));
  border-radius: 50%;
}
.ua__meta {
  position: absolute;
  bottom: -15px; /* clear of the pulsing ring (which reaches ~-6px) */
  left: 50%;
  transform: translateX(-50%);
  z-index: 2; /* sit above the ring, never under it */
  font-size: 9px;
  line-height: 1;
  padding: 2px 4px;
  border-radius: 6px;
  background: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-on-primary));
  white-space: nowrap;
  box-shadow: 0 0 0 2px rgb(var(--v-theme-surface)); /* crisp edge over the ring */
}
.ua--working::after {
  content: '';
  position: absolute;
  inset: -3px;
  border-radius: 50%;
  border: 2px solid rgb(var(--v-theme-primary));
  animation: ua-pulse 1.6s ease-in-out infinite;
}
@keyframes ua-pulse {
  0%,
  100% {
    opacity: 0.35;
    transform: scale(1);
  }
  50% {
    opacity: 1;
    transform: scale(1.08);
  }
}
</style>
