<template>
  <v-container v-if="loading" class="d-flex justify-center align-center" style="min-height: 400px">
    <v-progress-circular indeterminate color="primary" size="64"></v-progress-circular>
  </v-container>

  <v-container v-else-if="error" class="d-flex justify-center align-center flex-column" style="min-height: 400px">
    <v-alert type="error" :title="t('tasks.loadError.title')" :text="error"></v-alert>
    <BaseButton kind="secondary" class="mt-4" @click="$emit('retry')">{{ t('tasks.loadError.retry') }}</BaseButton>
  </v-container>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'

const { t } = useI18n()
defineProps<{
  loading: boolean
  error: string | null
}>()

defineEmits<{
  (e: 'retry'): void
}>()
</script>
