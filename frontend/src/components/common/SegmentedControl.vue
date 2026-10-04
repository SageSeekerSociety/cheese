<script setup lang="ts" generic="T extends string">
// 一排互斥选项里挑一个：外观（跟随系统 / 浅色 / 深色）、语言（中文 / English）。
// 底是一条 --fill 的槽，选中的那一格浮起成 --surface —— 选中靠明暗，不靠描边，也
// 不用琥珀：这是偏好，不是这一屏的主操作。
defineProps<{
  modelValue: T
  // `disabled`: shown but not choosable here (a teammate's model that does not
  // take that thinking effort); `title` says why.
  options: ReadonlyArray<{
    value: T
    label: string
    ariaLabel?: string
    lang?: string
    disabled?: boolean
    title?: string
  }>
  label: string
  // md 和 32px 的输入框、下拉并排时用（题目列表的工具栏）；默认那一号是偏好设置里的。
  size?: 'sm' | 'md'
}>()

const emit = defineEmits<{ 'update:modelValue': [value: T] }>()
</script>

<template>
  <div class="segmented" :class="{ 'segmented--md': size === 'md' }" role="radiogroup" :aria-label="label">
    <button
      v-for="option in options"
      :key="option.value"
      type="button"
      role="radio"
      class="segmented__option"
      :class="{ 'segmented__option--on': option.value === modelValue }"
      :aria-checked="option.value === modelValue"
      :aria-label="option.ariaLabel ?? option.label"
      :lang="option.lang"
      :disabled="option.disabled"
      :title="option.title"
      @click.stop="emit('update:modelValue', option.value)"
    >
      {{ option.label }}
    </button>
  </div>
</template>

<style scoped>
.segmented {
  display: inline-flex;
  gap: 2px;
  padding: 2px;
  background: var(--fill-2);
  border-radius: var(--radius-md);
}

.segmented__option {
  height: 24px;
  padding: 0 8px;
  font: inherit;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
  white-space: nowrap;
  cursor: pointer;
  background: transparent;
  border: 0;
  border-radius: var(--radius-sm);
  transition:
    background-color var(--dur-quick) var(--ease-standard),
    color var(--dur-quick) var(--ease-standard);
}

.segmented--md .segmented__option {
  height: 28px;
  padding: 0 12px;
  font-size: 13px;
  line-height: var(--lh-13);
}

.segmented__option:hover:not(:disabled) {
  color: var(--text);
}

.segmented__option:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}

.segmented__option--on {
  font-weight: 600;
  color: var(--ink);
  background: var(--surface);
}

.segmented__option:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 1px;
}
</style>
