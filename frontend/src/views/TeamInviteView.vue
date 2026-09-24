<script setup lang="ts">
// 小队链接的落地页：看到小队，然后加入（小队开着审批时是申请）。
import type { Team } from '@/types'

import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import TeamProfile from './teams/TeamProfile.vue'

import { authToken } from '@/api'
import { t } from '@/i18n'
import { TeamsApi } from '@/network/api/teams'
import { BusinessError } from '@/network/types/error'

const route = useRoute()
const router = useRouter()
const team = ref<Team | null>(null)
const busy = ref(false)
const error = ref('')
const invalid = ref(false)
const needsLogin = ref(!authToken())
let loadSequence = 0

function signIn() {
  void router.push({ name: 'SignIn', query: { redirect: route.fullPath } })
}

async function load() {
  const sequence = ++loadSequence
  team.value = null
  error.value = ''
  invalid.value = false
  if (needsLogin.value) return
  busy.value = true
  try {
    const {
      data: { team: result },
    } = await TeamsApi.detailByJoinLink(String(route.params.token))
    if (sequence === loadSequence) team.value = result
  } catch (e) {
    if (sequence !== loadSequence) return
    invalid.value = e instanceof BusinessError && e.code === 404
    error.value = invalid.value ? t('work.teamProfile.invalid') : t('work.teamProfile.failed')
  } finally {
    if (sequence === loadSequence) busy.value = false
  }
}

async function join(message: string) {
  const {
    data: { team: result },
  } = await TeamsApi.joinByJoinLink(String(route.params.token), { message: message || undefined })
  team.value = result
}

watch(() => route.params.token, load, { immediate: true })
</script>

<template>
  <v-container class="fill-height justify-center pa-4" fluid>
    <TeamProfile v-if="team" :team="team" :join="join" />
    <v-card v-else class="pa-6" max-width="560" width="100%" rounded="lg" flat border>
      <div class="t-eyebrow c-muted mb-2">{{ t('work.teamProfile.eyebrow') }}</div>
      <h1 class="t-page-title mb-4">{{ t('work.teamProfile.joinTitle') }}</h1>
      <template v-if="needsLogin">
        <p class="t-body c-muted mb-6">{{ t('work.teamProfile.loginHint') }}</p>
        <v-btn color="primary" variant="flat" @click="signIn">{{ t('work.teamProfile.login') }}</v-btn>
      </template>
      <v-alert v-else-if="error" type="error" class="mb-4">{{ error }}</v-alert>
      <v-progress-linear v-if="busy" indeterminate :aria-label="t('work.teamProfile.loading')" />
      <v-btn v-else-if="error && !invalid" variant="text" @click="load">{{ t('work.teamProfile.retry') }}</v-btn>
    </v-card>
  </v-container>
</template>
