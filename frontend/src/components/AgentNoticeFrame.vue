<script setup lang="ts">
import CheeseAvatar from './CheeseAvatar.vue'

defineProps<{ name: string | null; time: string }>()
</script>

<template>
  <div v-if="name" class="agent-status">
    <CheeseAvatar :size="28" :name="name" />
    <div class="agent-status-main">
      <div class="agent-status-meta">
        <span class="im-name">{{ name }}</span>
        <span>运行状态</span>
        <span class="agent-status-time">{{ time }}</span>
      </div>
      <div class="agent-status-body"><slot /></div>
    </div>
  </div>
  <slot v-else />
</template>

<style scoped>
.agent-status {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 4px calc(16px + 8%) 4px 16px;
  margin-top: 8px;
}
.agent-status-main {
  flex: 1;
  min-width: 0;
}
.agent-status-meta {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 2px;
  color: var(--muted);
  font-size: 12px;
}
.im-name {
  color: var(--ink);
  font-size: 13px;
  font-weight: 600;
}
.agent-status-time {
  font-family: var(--font-mono);
  color: var(--faint);
}
.agent-status-body {
  display: inline-block;
  max-width: 100%;
  padding: 6px 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  color: var(--muted);
  font-size: 13px;
  line-height: 1.6;
}
.agent-status-body :deep(.sys-row) {
  padding: 0;
}
.agent-status-body :deep(.sys-line) {
  justify-content: flex-start;
  flex-wrap: wrap;
}
.agent-status-body :deep(.sys-text) {
  min-width: 0;
  overflow-wrap: anywhere;
}
.agent-status-body :deep(.sys-detail) {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
