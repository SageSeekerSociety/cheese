<script setup lang="ts">
defineProps<{ title: string; lede?: string }>()
</script>

<template>
  <header class="account-heading">
    <h1 class="t-page-title">{{ title }}</h1>
    <p v-if="lede || $slots.default" class="account-heading__lede t-body">
      <slot>{{ lede }}</slot>
    </p>
  </header>
</template>

<style scoped>
.account-heading {
  margin-bottom: 32px;
}

.account-heading__lede {
  margin-top: 8px;
  color: var(--muted);
}
</style>
