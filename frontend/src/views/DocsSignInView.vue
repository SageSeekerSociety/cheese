<script setup lang="ts">
// The platform's half of signing in to the docs site. The docs are on a host of
// their own and cannot read this app's sign-in, so they send the reader here;
// this page asks for a grant (30 seconds, one use) and posts it to the docs host,
// which turns it into a cookie of its own and returns the reader to `path`.
// Not signed in here yet: sign in first, then come back to this page.
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ApiError, authToken } from '../api'
import { requestDocsGrant } from '../api/docs'
import { t } from '../i18n'

import BaseButton from '@/components/base/BaseButton.vue'

const route = useRoute()
const router = useRouter()
const loading = ref(false)
const error = ref('')

function signInFirst() {
  void router.replace({ name: 'SignIn', query: { redirect: route.fullPath } })
}

async function openDocs() {
  loading.value = true
  error.value = ''
  try {
    const grant = await requestDocsGrant()
    // A POST keeps the grant out of URLs, history and referrers.
    const form = document.createElement('form')
    form.method = 'POST'
    form.action = grant.url
    for (const [name, value] of [
      ['grant', grant.grant],
      ['path', typeof route.query.path === 'string' ? route.query.path : '/'],
    ]) {
      const input = document.createElement('input')
      input.type = 'hidden'
      input.name = name
      input.value = value
      form.append(input)
    }
    document.body.append(form)
    form.submit()
    form.remove()
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) signInFirst()
    else error.value = e instanceof Error ? e.message : t('project.open.docs.failed')
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  if (authToken()) void openDocs()
  else signInFirst()
})
</script>

<template>
  <v-container class="py-8">
    <!-- 手机上页名写在顶栏里，这里不再写一遍。 -->
    <h1 v-if="$vuetify.display.mdAndUp" class="t-page-title mb-4">{{ t('project.open.docs.title') }}</h1>
    <template v-if="error">
      <v-alert type="error" class="mb-4">{{ error }}</v-alert>
      <BaseButton kind="secondary" :loading="loading" @click="openDocs">{{ t('project.open.retry') }}</BaseButton>
    </template>
    <v-progress-circular v-else indeterminate :aria-label="t('project.open.docs.opening')" />
  </v-container>
</template>
