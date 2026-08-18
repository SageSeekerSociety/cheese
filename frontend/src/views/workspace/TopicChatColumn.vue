<script setup lang="ts">
import type { ProjectMemberRow, Topic } from '@/cx_types'
import type { CardPhase } from '@/lib/topicState'

import { computed, ref } from 'vue'

import ChatPanel from '@/components/ChatPanel.vue'
import TopicAcceptCard from '@/components/TopicAcceptCard.vue'
import TopicAgentPicker from '@/components/TopicAgentPicker.vue'
import TopicComputePicker from '@/components/TopicComputePicker.vue'

// 话题的对话那一半：时间线 + 输入框 + 末尾的采纳框 + 输入框旁边的 chips。
//
// 它是一个组件而不是 TopicView 模板里的一段，只因为它要挂在两个地方：桌面上是
// 左边那一栏，手机上是工作面板 tab 栏里「对话」那一格的内容（一屏放不下两栏）。
// 同一份接线写两遍是这两处早晚长歪的原因，所以它只写一遍。
defineOptions({ name: 'TopicChatColumn' })

defineProps<{
  topic: Topic
  members: ProjectMemberRow[]
  topicList: Topic[]
}>()

const emit = defineEmits<{
  (e: 'turn-done'): void
  (e: 'tool-used', payload: unknown): void
  (e: 'state-changed', payload: unknown): void
  (e: 'mention-click', handle: string): void
  (e: 'open-file', path: string): void
  (e: 'open-resource', resource: string): void
  (e: 'upgrade-message', payload: unknown): void
  (e: 'open-topic', topicId: string): void
  // 话题此刻处在哪一段，由采纳框说了算——头部的状态词和面板开在哪一格都读它。
  (e: 'phase', phase: CardPhase): void
  // 采纳框上的「去看改动」：面板换到 改动 那一格。
  (e: 'review'): void
}>()

const chatRef = ref<{ connected: boolean } | null>(null)
const acceptRef = ref<{ reload: (silent?: boolean) => Promise<void> } | null>(null)

const connected = computed(() => !!chatRef.value?.connected)

defineExpose({
  connected,
  reloadAccept: (silent?: boolean) => acceptRef.value?.reload(silent),
})
</script>

<template>
  <ChatPanel
    ref="chatRef"
    :topic="topic"
    hide-header
    show-composer
    :members="members"
    :topic-list="topicList"
    @turn-done="emit('turn-done')"
    @tool-used="emit('tool-used', $event)"
    @state-changed="emit('state-changed', $event)"
    @mention-click="emit('mention-click', $event)"
    @open-file="emit('open-file', $event)"
    @open-resource="emit('open-resource', $event)"
    @upgrade-message="emit('upgrade-message', $event)"
    @open-topic="emit('open-topic', $event)"
  >
    <!-- 成果待采纳框，放在对话时间线末尾 (GitHub PR 的合并框样式) -->
    <template #timeline-end>
      <TopicAcceptCard
        ref="acceptRef"
        :topic-id="topic.id"
        :topic-status="topic.status"
        @phase="emit('phase', $event)"
        @review="emit('review')"
      />
    </template>
    <!-- 话题自己的 chips: what this box is addressing, and where this topic's
         turns will run. 算力 locks on the first message, so it belongs beside
         the input that sends it. -->
    <template #composer-chips>
      <span v-if="topic.status === 'archived'" class="d-inline-flex align-center ga-1 c-faint archived-chip">
        <span class="status-dot status-dot--muted" />已归档
      </span>
      <TopicAgentPicker :key="`agent-${topic.id}`" :topic-id="topic.id" :project-id="topic.project_id" />
      <TopicComputePicker :key="topic.id" :topic-id="topic.id" />
    </template>
  </ChatPanel>
</template>

<style scoped>
.archived-chip {
  font-size: 12px;
}
</style>
