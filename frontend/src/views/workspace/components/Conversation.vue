<!-- Zone 2: the active thread's messages + composer. The *process*. -->
<template>
  <div class="wa-conv">
    <div v-if="activeThread" class="wa-conv__header">
      <v-icon :icon="activeThread.kind === 'management' ? 'mdi-sitemap-outline' : 'mdi-forum-outline'" class="mr-2" />
      <span class="wa-conv__title">{{ activeThread.title }}</span>
      <v-chip v-if="activeThread.kind === 'management'" size="x-small" color="deep-purple" variant="tonal" class="ml-2">管理</v-chip>
      <v-spacer />
      <div class="wa-conv__members">
        <AgentAvatar v-for="m in activeThread.members" :key="m.id" :user="m" :size="28" @open-scene="ws.openScene" />
        <span class="text-caption text-medium-emphasis ml-1">{{ activeThread.memberCount }} 人</span>
      </div>
    </div>

    <div ref="scrollEl" class="wa-conv__scroll">
      <div v-if="ws.state.loadingMessages" class="wa-conv__empty">
        <v-progress-circular indeterminate color="primary" />
      </div>
      <template v-else>
        <MessageBubble
          v-for="m in ws.state.messages"
          :key="m.id"
          :message="m"
          @open-ref="ws.openRef"
          @open-scene="ws.openScene"
        />
        <div v-if="!ws.state.messages.length" class="wa-conv__empty text-medium-emphasis">还没有消息</div>
      </template>
    </div>

    <div class="wa-conv__composer">
      <v-textarea
        v-model="draft"
        placeholder="发送消息…（Enter 发送，Shift+Enter 换行）"
        rows="1"
        auto-grow
        max-rows="6"
        hide-details
        density="comfortable"
        variant="solo-filled"
        flat
        :disabled="!activeThread"
        @keydown.enter.exact.prevent="send"
      >
        <template #append-inner>
          <v-btn icon="mdi-send" size="small" variant="text" color="primary" :disabled="!draft.trim()" @click="send" />
        </template>
      </v-textarea>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'

import { useWorkspace } from '../useWorkspace'

import AgentAvatar from './AgentAvatar.vue'
import MessageBubble from './MessageBubble.vue'

const ws = useWorkspace()
const draft = ref('')
const scrollEl = ref<HTMLElement | null>(null)

const activeThread = computed(() => ws.state.threads.find((t) => t.id === ws.state.activeThreadId) ?? null)

async function send() {
  const text = draft.value
  draft.value = ''
  await ws.sendMessage(text)
  await scrollToBottom()
}

async function scrollToBottom() {
  await nextTick()
  if (scrollEl.value) scrollEl.value.scrollTop = scrollEl.value.scrollHeight
}

watch(() => ws.state.messages.length, scrollToBottom)
watch(() => ws.state.activeThreadId, scrollToBottom)
</script>

<style scoped>
.wa-conv {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-width: 0;
}
.wa-conv__header {
  display: flex;
  align-items: center;
  padding: 12px 16px;
  border-bottom: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
}
.wa-conv__title {
  font-weight: 600;
}
.wa-conv__members {
  display: flex;
  align-items: center;
  gap: 4px;
}
.wa-conv__scroll {
  flex: 1;
  overflow-y: auto;
  padding: 12px 16px;
}
.wa-conv__empty {
  display: flex;
  justify-content: center;
  padding: 40px 0;
}
.wa-conv__composer {
  padding: 10px 14px;
  border-top: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
}
</style>
