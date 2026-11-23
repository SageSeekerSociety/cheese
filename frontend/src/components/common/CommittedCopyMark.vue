<script setup lang="ts">
// Beside a task file's path: what is shown is the committed version, read off the forge, because
// no environment holds the task's copy. Looking does not start one; the first edit does, and once
// the environment's own copy is loaded the mark goes.
import { t } from '@/i18n'
</script>

<template>
  <span class="committed-mark">{{ t('work.room.environment.committed') }}</span>
</template>

<style scoped>
.committed-mark {
  flex: none;
  padding: 0 6px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-sm);
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  white-space: nowrap;
}
</style>
