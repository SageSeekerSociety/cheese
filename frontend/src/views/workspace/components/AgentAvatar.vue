<!-- A user/agent avatar. An agent gets a subtle badge; a *working* agent gets an
     animated ring hinting the 现场 (live session) can be opened by clicking. -->
<template>
  <div
    class="wa-avatar"
    :class="{ 'wa-avatar--agent': user.isAgent, 'wa-avatar--working': working, 'wa-avatar--clickable': user.isAgent }"
    :title="user.isAgent ? (working ? `${user.nickname} · 工作中，点击查看现场` : `${user.nickname} · 智能体`) : user.nickname"
    :style="{ width: `${size}px`, height: `${size}px` }"
    @click.stop="onClick"
  >
    <v-avatar :size="size" :color="user.isAgent ? 'deep-purple-lighten-1' : 'blue-grey-lighten-1'">
      <span class="text-white" :style="{ fontSize: `${Math.round(size * 0.42)}px` }">{{ initial }}</span>
    </v-avatar>
    <v-icon v-if="user.isAgent" class="wa-avatar__badge" :size="Math.round(size * 0.42)" icon="mdi-robot-happy" />
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

import type { UserSummary } from '@/network/api/workspace'

const props = withDefaults(defineProps<{ user: UserSummary; size?: number }>(), { size: 36 })
const emit = defineEmits<{ (e: 'open-scene', user: UserSummary): void }>()

const working = computed(() => props.user.isAgent && props.user.agentStatus === 'working')
const initial = computed(() => (props.user.nickname || props.user.username || '?').slice(0, 1))

function onClick() {
  if (props.user.isAgent) emit('open-scene', props.user)
}
</script>

<style scoped>
.wa-avatar {
  position: relative;
  display: inline-flex;
  border-radius: 50%;
}
.wa-avatar--clickable {
  cursor: pointer;
}
.wa-avatar__badge {
  position: absolute;
  right: -2px;
  bottom: -2px;
  color: rgb(var(--v-theme-primary));
  background: rgb(var(--v-theme-surface));
  border-radius: 50%;
}
.wa-avatar--working::after {
  content: '';
  position: absolute;
  inset: -3px;
  border-radius: 50%;
  border: 2px solid rgb(var(--v-theme-primary));
  animation: wa-pulse 1.6s ease-in-out infinite;
}
@keyframes wa-pulse {
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
