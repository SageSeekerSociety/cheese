<script setup lang="ts">
// What 芝士 is waiting for in this conversation, over the input box: one row
// each, what it will do and when, and a way to call it off.
import type { AgentWait } from '@/composables/useAgentWaits'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

defineProps<{ waits: AgentWait[] }>()
const emit = defineEmits<{ cancel: [id: string] }>()
</script>

<template>
  <ul class="aw" :aria-label="t('tasks.assistant.waits.label')">
    <li v-for="w in waits" :key="w.id" class="aw__row">
      <v-icon size="16" class="aw__icon" aria-hidden="true">mdi-timer-sand</v-icon>
      <span class="aw__text">
        <span class="aw__title">{{ w.title }}</span>
        <span class="aw__when">{{ w.when }}</span>
      </span>
      <BaseButton kind="ghost" size="sm" @click="emit('cancel', w.id)">
        {{ t('tasks.assistant.waits.cancel') }}
      </BaseButton>
    </li>
  </ul>
</template>

<style scoped>
.aw {
  display: flex;
  flex-direction: column;
  margin: 0 0 8px;
  padding: 0;
  list-style: none;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: var(--surface);
}
.aw__row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 4px 4px 12px;
}
.aw__row + .aw__row {
  border-top: 1px solid var(--line);
}
.aw__icon {
  flex: none;
  color: var(--faint);
}
.aw__text {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-width: 0;
  padding: 4px 0;
  font-size: 13px;
  line-height: var(--lh-13);
}
.aw__title {
  overflow: hidden;
  color: var(--text);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.aw__when {
  overflow: hidden;
  color: var(--muted);
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
