<template>
  <!-- Next to something 芝士 changed: says so, and undoes it. -->
  <span class="agent-mark" data-testid="agent-mark">
    <CheeseAvatar :size="14" aria-hidden="true" />
    <span>{{ t('tasks.agentLayer.filled') }}</span>
    <button
      type="button"
      class="agent-mark__undo"
      :aria-label="t('tasks.assistant.changes.undoOne', { field: label })"
      @click="emit('undo')"
    >
      {{ t('tasks.agentLayer.undo') }}
    </button>
  </span>
</template>

<script setup lang="ts">
// 芝士改过的地方旁边那一小行：「芝士已填写」和「撤销」。表单上的格子、以后别的页面上芝士
// 改过的地方都用它，长得一样、撤销的说法一样。撤销做什么由外面决定（一般是
// `/assistant/changes/{id}/undo`），这里只往上报。
import { useI18n } from 'vue-i18n'

import CheeseAvatar from '@/components/CheeseAvatar.vue'

defineProps<{
  /** 改的是哪一项（读屏念「撤销 X」）。 */
  label: string
}>()

const emit = defineEmits<{ undo: [] }>()

const { t } = useI18n()
</script>

<style scoped>
.agent-mark {
  display: inline-flex;
  gap: 6px;
  align-items: center;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  white-space: nowrap;
}

.agent-mark__undo {
  padding: 0 2px;
  color: var(--accent-ink);
  border-radius: var(--radius-sm);
}

.agent-mark__undo:hover {
  text-decoration: underline;
}
</style>
