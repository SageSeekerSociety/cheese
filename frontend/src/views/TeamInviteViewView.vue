<!--
  The rendering half of TeamInviteView.vue, split out by `pnpm run scene:split`: props in,
  events out. The page keeps the route, the requests and the stores.
-->

<script setup lang="ts">
import type { Team } from '@/types'

import TeamProfile from './teams/TeamProfile.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

defineProps<{
  team: Team | null
  join: (message: string) => Promise<void>
  needsLogin: boolean
  error: string
  busy: boolean
  invalid: boolean
}>()
const emit = defineEmits<{
  'sign-in': []
  load: []
}>()
</script>

<template>
  <v-container class="fill-height justify-center pa-4" fluid>
    <TeamProfile v-if="team" :team="team" :join="join" />
    <v-card v-else class="pa-6" max-width="560" width="100%" rounded="lg" flat border>
      <div class="t-eyebrow c-muted mb-2">{{ t('work.teamProfile.eyebrow') }}</div>
      <h1 class="t-page-title mb-4">{{ t('work.teamProfile.joinTitle') }}</h1>
      <template v-if="needsLogin">
        <p class="t-body c-muted mb-6">{{ t('work.teamProfile.loginHint') }}</p>
        <BaseButton kind="primary" @click="emit('sign-in')">{{ t('work.teamProfile.login') }}</BaseButton>
      </template>
      <v-alert v-else-if="error" type="error" class="mb-4">{{ error }}</v-alert>
      <v-progress-linear v-if="busy" indeterminate :aria-label="t('work.teamProfile.loading')" />
      <BaseButton v-else-if="error && !invalid" kind="secondary" @click="emit('load')">{{
        t('work.teamProfile.retry')
      }}</BaseButton>
    </v-card>
  </v-container>
</template>
