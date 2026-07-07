<!-- 可复用的「板块」外壳：标题栏（标题 + 右侧操作插槽）+ 内容区。
     「我的 Agent」页由多个这样的板块拼成，日后新增第 3、第 4 个板块只需再放一个
     <SectionBlock>，无需改动布局。 -->
<template>
  <section class="section-block">
    <header class="section-block__head">
      <div class="section-block__titles">
        <div class="section-block__title">
          <v-icon v-if="icon" :icon="icon" class="me-2" />
          {{ title }}
          <span v-if="count !== undefined" class="section-block__count">{{ count }}</span>
        </div>
        <div v-if="subtitle" class="section-block__subtitle">{{ subtitle }}</div>
      </div>
      <v-spacer />
      <div class="section-block__actions">
        <slot name="actions" />
      </div>
    </header>
    <div class="section-block__body">
      <slot />
    </div>
  </section>
</template>

<script setup lang="ts">
defineProps<{
  title: string
  subtitle?: string
  icon?: string
  count?: number
}>()
</script>

<style scoped lang="scss">
.section-block {
  margin-bottom: 32px;

  &__head {
    display: flex;
    align-items: flex-end;
    gap: 12px;
    margin-bottom: 16px;
  }

  &__title {
    display: flex;
    align-items: center;
    font-size: 18px;
    font-weight: 700;
  }

  &__count {
    margin-left: 8px;
    font-size: 14px;
    font-weight: 400;
    opacity: 0.5;
  }

  &__subtitle {
    margin-top: 4px;
    font-size: 13px;
    color: rgba(var(--v-theme-on-surface), 0.6);
  }

  &__actions {
    display: flex;
    align-items: center;
    gap: 8px;
  }
}
</style>
