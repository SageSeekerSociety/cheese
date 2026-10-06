<script setup lang="ts">
// 发题表单的一节：一行标题（右边可以放这一节自己的按钮），下面一栏字段，节与节之间
// 一条细线。和设置页同一个写法：白底上按间距和线分组，不再一节一张卡片。
//
// 标题从外面进来，这一件不说自己在画哪一节，所以它能单独摆在预览站里
// （`catalogTaskForm.ts`）。
defineProps<{
  /** 这一节的标题：调用方已经翻好的那句话。 */
  title: string
}>()

defineSlots<{
  default?: () => unknown
  /** 标题右边这一节自己的按钮（「从文件导入」「使用模板」）。 */
  actions?: () => unknown
}>()
</script>

<template>
  <section class="tfs">
    <header class="tfs__head">
      <h2 class="tfs__title t-title">{{ title }}</h2>
      <div v-if="$slots.actions" class="tfs__actions"><slot name="actions" /></div>
    </header>
    <div class="tfs__body"><slot /></div>
  </section>
</template>

<style scoped>
.tfs {
  padding: 24px 0;
  border-bottom: 1px solid var(--line);
}

.tfs__head {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  min-height: 32px;
  margin-bottom: 16px;
}

.tfs__title {
  margin: 0;
  color: var(--ink);
}

.tfs__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-left: auto;
}

.tfs__body {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
</style>
