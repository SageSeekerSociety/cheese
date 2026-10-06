<script setup lang="ts">
// What the platform's sign-in page for the docs shows (DocsSignIn.vue does the
// work): a spinner while it hands the reader over, or what went wrong and a retry.
import { t } from '../i18n'

import BaseButton from '@/components/base/BaseButton.vue'

defineProps<{ error: string; loading: boolean }>()
defineEmits<{ retry: [] }>()
</script>

<template>
  <v-container class="py-8">
    <!-- 手机上页名写在顶栏里，这里不再写一遍。 -->
    <h1 v-if="$vuetify.display.mdAndUp" class="t-page-title mb-4">{{ t('project.open.docs.title') }}</h1>
    <template v-if="error">
      <v-alert type="error" class="mb-4">{{ error }}</v-alert>
      <BaseButton kind="secondary" :loading="loading" @click="$emit('retry')">{{ t('project.open.retry') }}</BaseButton>
    </template>
    <v-progress-circular v-else indeterminate :aria-label="t('project.open.docs.opening')" />
  </v-container>
</template>
