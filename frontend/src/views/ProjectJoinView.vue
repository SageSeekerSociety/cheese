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

async function enter() {
  if (!preview.value) return
  busy.value = true
  error.value = ''
  try {
    const result = await joinProjectByLink(String(route.params.token))
    await store.refreshProjects()
    await router.push({ name: 'workspace-project', params: { projectId: result.project_id } })
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
          <p class="t-body c-muted mb-6">
            {{ preview.already_member ? t('work.joinLink.alreadyMember') : t('work.joinLink.joinHint') }}
          </p>
          <v-btn color="primary" variant="flat" :loading="busy" @click="enter">
            {{ preview.already_member ? t('work.joinLink.enter') : t('work.joinLink.join') }}
          </v-btn>
        </template>
        <v-progress-linear v-else-if="busy" indeterminate :aria-label="t('work.joinLink.loading')" />
        <v-btn v-else-if="error && !invalid" variant="text" @click="load">{{ t('work.joinLink.retry') }}</v-btn>
      </template>
    </v-card>
  </v-container>
</template>
