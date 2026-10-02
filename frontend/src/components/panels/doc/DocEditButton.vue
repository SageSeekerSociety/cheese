<script setup lang="ts">
// 文档里那几张小卡片（改写框、改后的条子、建议卡、改动卡）和横条上用的按钮。
// `strong` 是一组里唯一的那个主动作：深底，不用琥珀 —— 接受和全部接受会同时在屏幕上。
defineProps<{ strong?: boolean; disabled?: boolean }>()
defineEmits<{ (e: 'click'): void }>()
</script>

<template>
  <button
    type="button"
    class="doc-edit-btn"
    :class="{ 'doc-edit-btn--strong': strong }"
    :disabled="disabled"
    @click="$emit('click')"
  >
    <slot />
  </button>
</template>

<style scoped>
.doc-edit-btn {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 6px;
  height: 28px;
  padding: 0 12px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  background: var(--surface);
  color: var(--text);
  font: inherit;
  font-size: 13px;
  white-space: nowrap;
  cursor: pointer;
  transition:
    background var(--dur-quick) var(--ease-standard),
    color var(--dur-quick) var(--ease-standard);
}
.doc-edit-btn:hover:not(:disabled) {
  background: var(--fill);
}
.doc-edit-btn--strong {
  border-color: var(--ink);
  background: var(--ink);
  color: var(--surface);
}
.doc-edit-btn--strong:hover:not(:disabled) {
  background: var(--text);
}
.doc-edit-btn:disabled {
  opacity: 0.5;
  cursor: default;
}
.doc-edit-btn:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}
</style>
