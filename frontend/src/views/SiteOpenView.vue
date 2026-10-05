<script setup lang="ts">
// 网站落地页的容器：读地址里的项目，向后端要一份 Site 会话，再把 grant 用 POST
// 交给内容站（POST 让只读授权不进 URL、历史和 referrer）。画面在
// `SiteOpenViewView.vue`，只收 props、只发 `retry`。
import { ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { ApiError, authToken, requestSiteSession } from '../api'
import { t } from '../i18n'

import SiteOpenViewView from './SiteOpenViewView.vue'

const route = useRoute()
const loading = ref(false)
const error = ref('')
const needsLogin = ref(!authToken())

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
  <SiteOpenViewView :loading="loading" :error="error" :needs-login="needsLogin" @retry="openSite" />
</template>
