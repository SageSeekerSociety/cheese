<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { ApiError, authToken, requestSiteSession } from '../api'

const route = useRoute()
const loading = ref(false)
const error = ref('')
const needsLogin = ref(!authToken())
const loginLink = { name: 'SignIn' }

async function openSite() {
  const projectId = String(route.params.projectId)
  loading.value = true
  error.value = ''
  try {
    const session = await requestSiteSession(projectId)
    if (String(route.params.projectId) !== projectId) return
    // A POST keeps the read-only grant out of URLs, history and referrers.
    const form = document.createElement('form')
    form.method = 'POST'
    form.action = session.url
    const input = document.createElement('input')
    input.type = 'hidden'
    input.name = 'grant'
    input.value = session.grant
    form.append(input)
    if (typeof route.query.path === 'string') {
      const path = document.createElement('input')
      path.type = 'hidden'
      path.name = 'path'
      path.value = route.query.path
      form.append(path)
    }
    document.body.append(form)
    form.submit()
    form.remove()
  } catch (e) {
    if (String(route.params.projectId) !== projectId) return
    if (e instanceof ApiError && e.status === 401) needsLogin.value = true
    else error.value = e instanceof Error ? e.message : '网站打开失败'
  } finally {
    loading.value = false
  }
}

watch(
  () => route.params.projectId,
  () => {
    if (!needsLogin.value) void openSite()
  },
  { immediate: true }
)
</script>

<template>
  <v-container class="py-8">
    <h1 class="t-page-title mb-4">打开网站</h1>
    <template v-if="needsLogin">
      <p class="t-body mb-4">这个网站仅项目成员可访问，请先登录</p>
      <v-btn color="primary" :to="loginLink">登录</v-btn>
    </template>
    <template v-else-if="error">
      <v-alert type="error" class="mb-4">{{ error }}</v-alert>
      <v-btn variant="tonal" :loading="loading" @click="openSite">重试</v-btn>
    </template>
    <v-progress-circular v-else indeterminate aria-label="正在打开网站" />
  </v-container>
</template>
