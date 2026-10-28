<script setup lang="ts">
// 额度快用完时的一行提示：一个警示图标、一句标题、一句说明。用在团队额度页顶部
// 和项目设置的预算区域。颜色、圆角、字号都取现有令牌（design-system §1）：底色
// 用 --warn-wash，边框和图标用 --warn，正文用 --text。
//
// 它只是提醒：看到它时调用照常放行，只有真正用完才拒绝。
defineProps<{
  title: string
  body: string
}>()
</script>

<template>
  <div class="credit-warning" role="status">
    <span class="credit-warning__icon" aria-hidden="true">!</span>
    <div class="credit-warning__text">
      <span class="credit-warning__title">{{ title }}</span>
      <span class="credit-warning__body">{{ body }}</span>
    </div>
  </div>
</template>

<style scoped>
.credit-warning {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 10px;
  align-items: start;
  padding: 12px 14px;
  border: 1px solid var(--warn);
  border-radius: var(--radius-md);
  background: var(--warn-wash);
}

.credit-warning__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border-radius: var(--radius-sm);
  background: var(--warn);
  /* 实心警示块上的那个惊叹号：和侧栏角标同一套（`RailItem` 的 `--inverse-surface`）。 */
  color: var(--inverse-surface);
  font-size: 12px;
  font-weight: 700;
  line-height: 1;
}

.credit-warning__title {
  display: block;
  color: var(--ink);
  font-size: 14px;
  line-height: var(--lh-14);
  font-weight: 600;
}

.credit-warning__body {
  display: block;
  margin-top: 2px;
  color: var(--text);
  font-size: 13px;
  line-height: var(--lh-13);
}
</style>
