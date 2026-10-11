<script setup lang="ts">
// 预览落地页的容器：读地址里的房间，向后端要一份预览会话再交给内容站。画面在
// `PreviewOpenViewView.vue`，只收 props、只发 `retry`。
import { onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { ApiError, authToken, requestPreviewSession } from '../api'
import { t } from '../i18n'
import { postPreviewSession } from '../lib/previewSession'

import PreviewOpenViewView from './PreviewOpenViewView.vue'

const route = useRoute()
const loading = ref(false)
const error = ref('')
const needsLogin = ref(!authToken())
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
  <PreviewOpenViewView :loading="loading" :error="error" :needs-login="needsLogin" @retry="openPreview" />
</template>
