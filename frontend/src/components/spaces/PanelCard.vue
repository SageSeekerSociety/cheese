<script setup lang="ts">
// 一块面板。看板与列表里九成的容器都是它，所以边框、圆角、标题排版只写这一处。
defineProps<{
  title?: string
  subtitle?: string
  /** 标题右侧那一小块，放筛选或按钮。 */
  dense?: boolean
}>()
</script>

<template>
  <v-card flat rounded="lg" class="panel" :class="{ 'panel--dense': dense }">
    <header v-if="title || $slots.actions" class="panel__head">
      <div>
        <h3 v-if="title">{{ title }}</h3>
        <p v-if="subtitle" class="panel__sub">{{ subtitle }}</p>
      </div>
      <div class="panel__actions"><slot name="actions" /></div>
    </header>
    <slot />
  </v-card>
</template>

<style scoped lang="scss">
.panel {
  padding: 18px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  background-color: rgb(var(--v-theme-surface));
}

.panel--dense {
  padding: 12px;
}

.panel__head {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 14px;
}

.panel__head h3 {
  margin: 0;
  font-size: 0.95rem;
  font-weight: 600;
}

.panel__sub {
  margin: 4px 0 0;
  color: rgba(var(--v-theme-on-surface), 0.58);
  font-size: 0.8rem;
  line-height: 1.5;
}

.panel__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
</style>
