<!-- One chat message. References render as chips; clicking one navigates the
     right-hand context pane to that object. System messages (e.g. a claim
     announcement) render centered and muted. -->
<template>
  <div v-if="message.kind === 'system'" class="wa-sys text-caption text-medium-emphasis">
    <v-icon size="14" icon="mdi-information-outline" class="mr-1" />
    <span>{{ message.content }}</span>
    <template v-for="ref in message.refs" :key="ref.targetId">
      <a class="wa-sys__ref" @click="$emit('open-ref', ref)">{{ ref.title || `#${ref.targetId}` }}</a>
    </template>
  </div>

  <div v-else class="wa-msg" :class="{ 'wa-msg--mine': isMine }">
    <AgentAvatar :user="message.author" :size="36" @open-scene="$emit('open-scene', $event)" />
    <div class="wa-msg__body">
      <div class="wa-msg__meta">
        <span class="wa-msg__name">{{ message.author.nickname }}</span>
        <span v-if="message.author.isAgent" class="wa-msg__tag">智能体</span>
        <span class="wa-msg__time">{{ time }}</span>
      </div>
      <div class="wa-msg__bubble">
        <span class="wa-msg__text">{{ message.content }}</span>
        <div v-if="message.refs.length" class="wa-msg__refs">
          <v-chip
            v-for="ref in message.refs"
            :key="ref.targetId"
            size="small"
            variant="tonal"
            :color="refColor(ref.targetType)"
            :prepend-icon="refIcon(ref.targetType)"
            class="wa-msg__ref"
            @click="$emit('open-ref', ref)"
          >
            {{ ref.title || `#${ref.targetId}` }}
          </v-chip>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

import type { BlockRef, Message, RefTargetType, UserSummary } from '@/network/api/workspace'
import { currentUserId } from '@/services/account'

import AgentAvatar from './AgentAvatar.vue'

const props = defineProps<{ message: Message }>()
defineEmits<{ (e: 'open-ref', ref: BlockRef): void; (e: 'open-scene', user: UserSummary): void }>()

const isMine = computed(() => props.message.author.id === currentUserId.value)
const time = computed(() => {
  const d = new Date(props.message.createdAt)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
})

function refIcon(t: RefTargetType) {
  return t === 'document' ? 'mdi-file-document-outline' : t === 'workitem' ? 'mdi-checkbox-marked-circle-outline' : 'mdi-message-outline'
}
function refColor(t: RefTargetType) {
  return t === 'document' ? 'indigo' : t === 'workitem' ? 'teal' : 'blue-grey'
}
</script>

<style scoped>
.wa-msg {
  display: flex;
  gap: 10px;
  padding: 6px 4px;
  align-items: flex-start;
}
.wa-msg--mine {
  flex-direction: row-reverse;
}
.wa-msg__body {
  max-width: 78%;
}
.wa-msg--mine .wa-msg__body {
  text-align: right;
}
.wa-msg__meta {
  display: flex;
  gap: 6px;
  align-items: center;
  margin-bottom: 2px;
}
.wa-msg--mine .wa-msg__meta {
  flex-direction: row-reverse;
}
.wa-msg__name {
  font-size: 13px;
  font-weight: 600;
}
.wa-msg__tag {
  font-size: 10px;
  padding: 0 5px;
  border-radius: 6px;
  background: rgb(var(--v-theme-primary), 0.12);
  color: rgb(var(--v-theme-primary));
}
.wa-msg__time {
  font-size: 11px;
  color: rgba(var(--v-theme-on-surface), 0.5);
}
.wa-msg__bubble {
  display: inline-block;
  padding: 8px 12px;
  border-radius: 12px;
  background: rgba(var(--v-theme-on-surface), 0.05);
  text-align: left;
}
.wa-msg--mine .wa-msg__bubble {
  background: rgb(var(--v-theme-primary), 0.12);
}
.wa-msg__text {
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 14px;
}
.wa-msg__refs {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 6px;
}
.wa-msg__ref {
  cursor: pointer;
}
.wa-sys {
  text-align: center;
  padding: 6px 0;
}
.wa-sys__ref {
  margin-left: 4px;
  cursor: pointer;
  color: rgb(var(--v-theme-primary));
  text-decoration: underline;
}
</style>
