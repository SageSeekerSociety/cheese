<script setup lang="ts">
// 小队链接的落地页：看到小队，然后加入（小队开着审批时是申请）。
//
// 这一份是画的那一半：只收 props、只发事件。按地址打开时读哪一个 token、调哪条接口、
// 登录前先记住要来哪、加入成功后换成什么样的 team，都在容器 TeamInviteView.vue 里。
import type { UserRefTarget } from '@/lib/userRef'
import type { Team } from '@/types'

import TeamProfileView from './teams/TeamProfileView.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

defineProps<{
  team: Team | null
  join: (message: string) => Promise<void>
  busy: boolean
  error: string
  invalid: boolean
  needsLogin: boolean
  /** 一声 @ 那个人时去哪：名册/项目的判断在容器那边（useUserRef 的同一套）。 */
  userTo: (handle: string) => UserRefTarget | null
}>()

defineEmits<{
  'sign-in': []
  retry: []
  navigate: [target: UserRefTarget | null]
}>()
</script>

<template>
  <v-container class="fill-height justify-center pa-4" fluid>
    <TeamProfileView v-if="team" :team="team" :join="join" :user-to="userTo" @navigate="$emit('navigate', $event)" />
    <v-card v-else class="pa-6" max-width="560" width="100%" rounded="lg" flat border>
      <div class="t-eyebrow c-muted mb-2">{{ t('work.teamProfile.eyebrow') }}</div>
      <h1 class="t-page-title mb-4">{{ t('work.teamProfile.joinTitle') }}</h1>
      <template v-if="needsLogin">
        <p class="t-body c-muted mb-6">{{ t('work.teamProfile.loginHint') }}</p>
        <BaseButton kind="primary" @click="$emit('sign-in')">{{ t('work.teamProfile.login') }}</BaseButton>
      </template>
      <v-alert v-else-if="error" type="error" class="mb-4">{{ error }}</v-alert>
      <v-progress-linear v-if="busy" indeterminate :aria-label="t('work.teamProfile.loading')" />
      <BaseButton v-else-if="error && !invalid" kind="secondary" @click="$emit('retry')">{{
        t('work.teamProfile.retry')
      }}</BaseButton>
    </v-card>
  </v-container>
</template>
