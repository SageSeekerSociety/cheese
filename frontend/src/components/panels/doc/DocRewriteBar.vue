<script setup lang="ts">
// 改好之后贴在那一段下面的条子：改了，可以撤销，也可以接着改；撤销之后可以恢复。
import CheeseAvatar from '../../CheeseAvatar.vue'

import DocEditButton from './DocEditButton.vue'

import { t } from '@/i18n'

defineProps<{ agentName: string; undone: boolean; busy: boolean }>()
const emit = defineEmits<{
  (e: 'undo'): void
  (e: 'redo'): void
  (e: 'again'): void
}>()
</script>

<template>
  <div class="doc-rewrite-bar" role="status">
    <template v-if="!undone">
      <CheeseAvatar :size="20" :name="agentName" />
      <span class="doc-rewrite-bar__text">{{ t('work.room.docEdit.done') }}</span>
      <DocEditButton :disabled="busy" @click="emit('undo')">{{ t('work.room.docEdit.undo') }}</DocEditButton>
      <DocEditButton :disabled="busy" @click="emit('again')">{{ t('work.room.docEdit.again') }}</DocEditButton>
    </template>
    <template v-else>
      <span class="doc-rewrite-bar__text">{{ t('work.room.docEdit.undone', { agent: agentName }) }}</span>
      <DocEditButton :disabled="busy" @click="emit('redo')">{{ t('work.room.docEdit.redo') }}</DocEditButton>
    </template>
  </div>
</template>

<style scoped>
.doc-rewrite-bar {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  max-width: 100%;
  padding: 6px 6px 6px 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: var(--raised);
  box-shadow: var(--shadow-1);
}
.doc-rewrite-bar__text {
  color: var(--text);
  font-size: 13px;
  line-height: var(--lh-13);
}
</style>
