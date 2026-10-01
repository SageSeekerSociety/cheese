<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { ApiError, authToken, requestPreviewSession } from '../api'
import { t } from '../i18n'
import { postPreviewSession } from '../lib/previewSession'

const route = useRoute()
const loading = ref(false)
const error = ref('')
const needsLogin = ref(!authToken())
const loginLink = { name: 'SignIn' }
let generation = 0

async function openPreview() {
  const topicId = String(route.params.topicId)
  const current = ++generation
  loading.value = true
  error.value = ''
  try {
    const session = await requestPreviewSession(topicId)
    if (current !== generation) return
    postPreviewSession(session, { path: typeof route.query.path === 'string' ? route.query.path : undefined })
  } catch (e) {
    if (current !== generation) return
    if (e instanceof ApiError && e.status === 401) needsLogin.value = true
    else error.value = e instanceof Error ? e.message : t('project.open.preview.failed')
  } finally {
    if (current === generation) loading.value = false
  }
}

watch(
  () => route.fullPath,
  () => {
    generation += 1
    needsLogin.value = !authToken()
    if (!needsLogin.value) void openPreview()
  },
  { immediate: true }
)
onBeforeUnmount(() => {
  generation += 1
})
</script>

<template>
  <v-container class="py-8">
    <!-- 手机上页名写在顶栏里，这里不再写一遍。 -->
    <h1 v-if="$vuetify.display.mdAndUp" class="t-page-title mb-4">{{ t('project.open.preview.title') }}</h1>
    <template v-if="needsLogin">
      <p class="t-body mb-4">{{ t('project.open.preview.membersOnly') }}</p>
      <v-btn color="primary" :to="loginLink">{{ t('project.open.signIn') }}</v-btn>
    </template>
    <template v-else-if="error">
      <v-alert type="error" class="mb-4">{{ error }}</v-alert>
      <v-btn color="primary" variant="tonal" :loading="loading" @click="openPreview">{{
        t('project.open.retry')
      }}</v-btn>
    </template>
    <v-progress-circular v-else indeterminate :aria-label="t('project.open.preview.opening')" />
  </v-container>
</template>
