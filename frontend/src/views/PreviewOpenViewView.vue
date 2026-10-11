<script setup lang="ts">
// The picture for the Preview landing page: title, sign-in hint, error and the
// opening spinner. Opening is not here — the container `PreviewOpenView.vue`
// reads the address, asks for a preview session and posts it. This half only
// receives props and emits `retry`.
import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

defineProps<{
  loading: boolean
  error: string
  needsLogin: boolean
}>()

defineEmits<{
  retry: []
}>()

const loginLink = { name: 'SignIn' }
</script>

<template>
  <v-container class="py-8">
    <!-- On phones the page title is in the top bar; do not repeat it here. -->
    <h1 v-if="$vuetify.display.mdAndUp" class="t-page-title mb-4">{{ t('project.open.preview.title') }}</h1>
    <template v-if="needsLogin">
      <p class="t-body mb-4">{{ t('project.open.preview.membersOnly') }}</p>
      <BaseButton kind="primary" :to="loginLink">{{ t('project.open.signIn') }}</BaseButton>
    </template>
    <template v-else-if="error">
      <v-alert type="error" class="mb-4">{{ error }}</v-alert>
      <BaseButton kind="secondary" :loading="loading" @click="$emit('retry')">{{ t('project.open.retry') }}</BaseButton>
    </template>
    <v-progress-circular v-else indeterminate :aria-label="t('project.open.preview.opening')" />
  </v-container>
</template>
