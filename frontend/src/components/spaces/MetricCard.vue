<script setup lang="ts">
// KPI 数字卡。看板最上面那一行。
//
// 刻意**不**给数字上主色（amber）：设计系统把 amber 留给「唯一的主操作按钮」，
// 一排数字全染成 amber 会让整页失去焦点（plugins/vuetify.ts 顶部那条原则）。
// 需要表达的强弱靠字号和灰阶，不靠颜色。
defineProps<{
  label: string
  value: string | number
  /** 副行说明，比如「较上周 +12%」。 */
  hint?: string
  /** 副行的语气：好 / 坏 / 中性。 */
  tone?: 'ok' | 'warn' | 'danger' | 'muted'
  icon?: string
}>()
</script>

<template>
  <div class="metric">
    <div class="metric__top">
      <v-icon v-if="icon" :icon="icon" size="18" class="metric__icon" />
      <span class="metric__label">{{ label }}</span>
    </div>
    <div class="metric__value">{{ value }}</div>
    <div v-if="hint" class="metric__hint" :class="`metric__hint--${tone ?? 'muted'}`">{{ hint }}</div>
  </div>
</template>

<style scoped lang="scss">
.metric {
  padding: 14px 16px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  border-radius: 12px;
  background-color: rgb(var(--v-theme-surface));
}

.metric__top {
  display: flex;
  gap: 6px;
  align-items: center;
}

.metric__icon {
  color: rgba(var(--v-theme-on-surface), 0.45);
}

.metric__label {
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 0.8rem;
}

.metric__value {
  margin-top: 8px;
  font-size: 1.6rem;
  font-weight: 650;
  line-height: 1.1;
}

.metric__hint {
  margin-top: 6px;
  font-size: 0.76rem;
}

.metric__hint--ok {
  color: rgb(var(--v-theme-success));
}

.metric__hint--warn {
  color: rgb(var(--v-theme-warning));
}

.metric__hint--danger {
  color: rgb(var(--v-theme-error));
}

.metric__hint--muted {
  color: rgba(var(--v-theme-on-surface), 0.5);
}
</style>
