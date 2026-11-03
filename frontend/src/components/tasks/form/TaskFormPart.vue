<script setup lang="ts">
// 发题表单的一大块：「题目内容」「AI 的设置」「发布设置」。块名比块里的节名（15px）
// 大一档，下面是这一块的几节（`TaskFormSection`），或者直接是这一块的字段。
//
// 和 `TaskFormSection` 一样只画、不说自己是哪一块：标题、说明都从外面进来。
defineProps<{
  /** 这一块的名字：调用方已经翻好的那句话。 */
  title: string
  /** 名字下面的一句说明；没有就不画。 */
  hint?: string
}>()

defineSlots<{
  default?: () => unknown
}>()
</script>

<template>
  <section class="tfp">
    <header class="tfp__head">
      <h2 class="tfp__title">{{ title }}</h2>
      <p v-if="hint" class="tfp__hint t-meta-read">{{ hint }}</p>
    </header>
    <slot />
  </section>
</template>

<style scoped>
.tfp {
  padding-top: 32px;
}

.tfp:first-child {
  padding-top: 8px;
}

.tfp__head {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.tfp__title {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  line-height: var(--lh-18);
  color: var(--ink);
}

.tfp__hint {
  margin: 0;
}
</style>
