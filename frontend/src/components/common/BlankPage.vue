<template>
  <BaseEmptyState icon="mdi-book-alert-outline" :title="t('shell.empty')" />
</template>

<script setup lang="ts">
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import { t } from '@/i18n'
</script>
