<script setup lang="ts">
import type { ProjectJoinPreview } from '@/api'

import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ApiError, authToken, joinProjectByLink, previewProjectJoinLink } from '@/api'
import { t } from '@/i18n'
import { useWorkspaceStore } from '@/stores/workspace'

const route = useRoute()
const router = useRouter()
const store = useWorkspaceStore()
const preview = ref<ProjectJoinPreview | null>(null)
const busy = ref(false)
const error = ref('')
const needsLogin = ref(!authToken())
const invalid = ref(false)
const reason = ref('')
let loadSequence = 0

function signIn() {
  void router.push({ name: 'SignIn', query: { redirect: route.fullPath } })
}

function failure(e: unknown) {
  if (e instanceof ApiError && e.status === 401) {
    needsLogin.value = true
  } else {
    invalid.value = e instanceof ApiError && e.status === 404
    error.value = invalid.value ? t('work.joinLink.invalid') : t('work.joinLink.failed')
  }
}

async function load() {
  const sequence = ++loadSequence
  preview.value = null
  error.value = ''
  invalid.value = false
  if (needsLogin.value) return
  busy.value = true
  try {
    const result = await previewProjectJoinLink(String(route.params.token))
    if (sequence === loadSequence) preview.value = result
  } catch (e) {
    if (sequence === loadSequence) failure(e)
  } finally {
    if (sequence === loadSequence) busy.value = false
  }
}

async function open(projectId: string) {
  await store.refreshProjects()
  await router.push({ name: 'workspace-project', params: { projectId } })
}

// 同一颗按钮三种意思：已经是成员就进去；链接不要审批就直接加入并进去；要审批就
// 递上申请，停在这一页显示「等待审批」——那时候项目还进不去。
async function enter() {
  if (!preview.value) return
  busy.value = true
  error.value = ''
  try {
    if (preview.value.join_status === 'member') {
      await open(preview.value.project_id)
      return
    }
    const result = await joinProjectByLink(String(route.params.token), reason.value.trim() || undefined)
    if (result.join_status === 'member') await open(result.project_id)
    else preview.value = result
  } catch (e) {
    failure(e)
  } finally {
    busy.value = false
  }
}

watch(() => route.params.token, load, { immediate: true })
</script>

<template>
  <v-container class="fill-height justify-center pa-4" fluid>
    <v-card class="pa-6" max-width="520" width="100%" rounded="lg" flat border>
      <div class="t-eyebrow c-muted mb-2">{{ t('work.joinLink.title') }}</div>
      <h1 class="t-page-title mb-4">{{ t('work.joinLink.joinTitle') }}</h1>
      <template v-if="needsLogin">
        <p class="t-body c-muted mb-6">{{ t('work.joinLink.loginHint') }}</p>
        <v-btn color="primary" variant="flat" @click="signIn">{{ t('work.joinLink.login') }}</v-btn>
      </template>
      <template v-else>
        <v-alert v-if="error" type="error" class="mb-4">{{ error }}</v-alert>
        <template v-if="preview && !invalid">
          <h2 class="t-title mb-3">{{ preview.project_name }}</h2>
          <template v-if="preview.join_status === 'pending'">
            <p class="t-body mb-2">{{ t('work.joinLink.pending') }}</p>
            <p class="t-body c-muted">{{ t('work.joinLink.pendingHint') }}</p>
          </template>
          <template v-else-if="preview.join_status === 'member'">
            <p class="t-body c-muted mb-6">{{ t('work.joinLink.alreadyMember') }}</p>
            <v-btn color="primary" variant="flat" :loading="busy" @click="enter">{{ t('work.joinLink.enter') }}</v-btn>
          </template>
          <template v-else-if="preview.approval">
            <p class="t-body c-muted mb-4">{{ t('work.joinLink.applyHint') }}</p>
            <v-textarea
              v-model="reason"
              autocomplete="off"
              :label="t('work.joinLink.reason')"
              variant="outlined"
              rows="3"
              auto-grow
              hide-details
              class="mb-4"
            />
            <v-btn color="primary" variant="flat" :loading="busy" @click="enter">{{ t('work.joinLink.apply') }}</v-btn>
          </template>
          <template v-else>
            <p class="t-body c-muted mb-6">{{ t('work.joinLink.joinHint') }}</p>
            <v-btn color="primary" variant="flat" :loading="busy" @click="enter">{{ t('work.joinLink.join') }}</v-btn>
          </template>
        </template>
        <v-progress-linear v-else-if="busy" indeterminate :aria-label="t('work.joinLink.loading')" />
        <v-btn v-else-if="error && !invalid" variant="text" @click="load">{{ t('work.joinLink.retry') }}</v-btn>
      </template>
    </v-card>
  </v-container>
</template>
