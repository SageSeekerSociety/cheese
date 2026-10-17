<script setup lang="ts">
// 小队链接的落地页：看到小队，然后加入（小队开着审批时是申请）。
import type { Team } from '@/types'

import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import TeamInviteViewView from './TeamInviteViewView.vue'

import { ApiError, authToken } from '@/api'
import { TeamsApi } from '@/api/teams'
import { t } from '@/i18n'

const props = defineProps<{ token: string }>()

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
    const { team: result } = await TeamsApi.detailByJoinLink(String(props.token))
    if (sequence === loadSequence) team.value = result
  } catch (e) {
    if (sequence !== loadSequence) return
    invalid.value = e instanceof ApiError && e.status === 404
    error.value = invalid.value ? t('work.teamProfile.invalid') : t('work.teamProfile.failed')
  } finally {
    if (sequence === loadSequence) busy.value = false
  }
}

async function join(message: string) {
  const { team: result } = await TeamsApi.joinByJoinLink(String(props.token), { message: message || undefined })
  team.value = result
}

watch(() => props.token, load, { immediate: true })
</script>

<template>
  <TeamInviteViewView
    :team="team"
    :join="join"
    :needs-login="needsLogin"
    :error="error"
    :busy="busy"
    :invalid="invalid"
    @sign-in="signIn"
    @load="load"
  />
</template>
