<script setup lang="ts">
// 撰写框上那一行「芝士会看到：你在看 X」（lib/viewingContext.ts）。
//
// 写出来，是因为随消息带走的东西读者应该看得见；能点，是因为他可以不想带。点一下
// 就不带（划线），再点一下带回来；选择记在这台浏览器上。这条消息已经指了一处（批注、
// 引用）时不显示：那时不会再叠一份「在看哪个文件」。
import { computed, inject } from 'vue'

import { fileName, useShareViewing, VIEWING_CONTEXT } from '../../lib/viewingContext'

import { t } from '@/i18n'

const props = defineProps<{ suppressed?: boolean }>()
const viewing = inject(VIEWING_CONTEXT, null)
const { share, toggle } = useShareViewing()
const name = computed(() => (viewing?.value ? fileName(viewing.value.path) : ''))
const label = computed(() =>
  share.value ? t('work.room.composer.viewingShared', { name: name.value }) : t('work.room.composer.viewingOff')
)
</script>

<template>
  <button
    v-if="name && !props.suppressed"
    type="button"
    class="composer-viewing"
    :class="{ 'composer-viewing--off': !share }"
    data-testid="composer-viewing"
    :aria-pressed="share"
    :title="share ? t('work.room.composer.viewingTurnOff') : t('work.room.composer.viewingTurnOn', { name })"
    @click="toggle"
  >
    <v-icon size="14">{{ share ? 'mdi-eye-outline' : 'mdi-eye-off-outline' }}</v-icon>
    <span class="composer-viewing__text">{{ label }}</span>
  </button>
</template>

<style scoped>
button.composer-viewing {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
  max-width: 260px;
  height: 24px;
  padding: 0 8px;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
  background: var(--fill);
  border-radius: var(--radius-pill);
  cursor: pointer;
}
button.composer-viewing:hover {
  color: var(--text);
  background: var(--fill-2);
}
button.composer-viewing:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 1px;
}
.composer-viewing__text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.composer-viewing--off .composer-viewing__text {
  text-decoration: line-through;
}
</style>
