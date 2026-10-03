<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { ApiError, authToken, requestSiteSession } from '../api'
import { t } from '../i18n'

import BaseButton from '@/components/base/BaseButton.vue'

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
    else error.value = e instanceof Error ? e.message : t('project.open.site.failed')
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
    <!-- 手机上页名写在顶栏里，这里不再写一遍。 -->
    <h1 v-if="$vuetify.display.mdAndUp" class="t-page-title mb-4">{{ t('project.open.site.title') }}</h1>
    <template v-if="needsLogin">
      <p class="t-body mb-4">{{ t('project.open.site.membersOnly') }}</p>
      <BaseButton kind="primary" :to="loginLink">{{ t('project.open.signIn') }}</BaseButton>
    </template>
    <template v-else-if="error">
      <v-alert type="error" class="mb-4">{{ error }}</v-alert>
      <BaseButton kind="secondary" :loading="loading" @click="openSite">{{ t('project.open.retry') }}</BaseButton>
    </template>
    <v-progress-circular v-else indeterminate :aria-label="t('project.open.site.opening')" />
  </v-container>
</template>
