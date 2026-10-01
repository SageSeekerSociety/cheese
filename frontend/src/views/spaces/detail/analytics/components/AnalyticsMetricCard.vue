<template>
  <!-- 一格指标：名字、数字、一句说明。几格并排由 AnalyticsStatStrip 排成一条，细线隔开。 -->
  <div class="metric">
    <div class="metric__label">{{ label }}</div>
    <div class="metric__value t-num">{{ value }}</div>
    <div class="metric__foot">{{ description }}</div>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  label: string
  value: string | number
  description: string
}>()
</script>

<style scoped>
.metric {
  min-width: 0;
  padding: 14px 16px;
}

.metric__label {
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
}

.metric__value {
  margin: 2px 0;
  color: var(--ink);
  font-size: 23px;
  font-weight: 650;
  line-height: var(--lh-23);
}

.metric__foot {
  color: var(--faint);
  font-size: 12px;
  line-height: var(--lh-12);
}
</style>
