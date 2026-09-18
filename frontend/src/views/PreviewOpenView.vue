<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { ApiError, authToken, requestPreviewSession } from '../api'
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
    else error.value = e instanceof Error ? e.message : '预览打开失败'
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
    <h1 class="t-page-title mb-4">打开预览</h1>
    <template v-if="needsLogin">
      <p class="t-body mb-4">这个预览仅项目成员可访问，请先登录</p>
      <v-btn color="primary" :to="loginLink">登录</v-btn>
    </template>
    <template v-else-if="error">
      <v-alert type="error" class="mb-4">{{ error }}</v-alert>
      <v-btn variant="tonal" :loading="loading" @click="openPreview">重试</v-btn>
    </template>
    <v-progress-circular v-else indeterminate aria-label="正在打开预览" />
  </v-container>
</template>
