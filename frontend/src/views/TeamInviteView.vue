<script setup lang="ts">
// 小队链接的落地页：看到小队，然后加入（小队开着审批时是申请）。
//
// 这一份是容器：路由、按链接取小队、加入、登录前先留个念想到哪都在这里；画的那一半在
// TeamInviteViewView.vue。
import type { UserRefTarget } from '@/lib/userRef'
import type { Team } from '@/types'

import { getCurrentInstance, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import TeamInviteViewView from './TeamInviteViewView.vue'

import { authToken } from '@/api'
import { t } from '@/i18n'
import { userRefRoute } from '@/lib/userRef'
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

// 小队所有者那颗 @ 去哪：没有装路由（单测里）就不给去处，照样画 @名字。
const app = getCurrentInstance()?.appContext.config.globalProperties

function userTo(handle: string): UserRefTarget | null {
  return app?.$router ? userRefRoute(handle) : null
}

function navigate(target: UserRefTarget | null) {
  if (target) void router.push(target)
}

watch(() => route.params.token, load, { immediate: true })
</script>

<template>
  <TeamInviteViewView
    :team="team"
    :join="join"
    :busy="busy"
    :error="error"
    :invalid="invalid"
    :needs-login="needsLogin"
    :user-to="userTo"
    @sign-in="signIn"
    @retry="load"
    @navigate="navigate"
  />
</template>
