<script setup lang="ts">
defineProps<{ who: string; ai?: boolean }>()
</script>

<template>
  <div class="message">
    <span class="message-avatar" :class="{ 'ai-avatar': ai }" aria-hidden="true">
      <v-icon v-if="ai" icon="mdi-creation" size="16" />
      <template v-else>{{ who[0] }}</template>
    </span>
    <div>
      <div class="message-author">{{ who }} <small v-if="ai">AI 队友</small></div>
      <slot />
    </div>
  </div>
</template>

<style scoped>
.message-avatar {
  display: grid;
  width: 29px;
  height: 29px;
  font-size: 13px;
  color: var(--muted);
  background: var(--fill);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  flex-shrink: 0;
  place-items: center;
}
</style>
