<script setup lang="ts">
import { computed, inject, ref, watch } from 'vue'

import { useCreditOffers } from '@/composables/useCreditOffers'
import { useCreditUsage } from '@/composables/useCreditUsage'

import CreditsView from './CreditsView.vue'

import { getTeamCreditUsage } from '@/api/creditUsage'
import { teamDataInjectionKey } from '@/keys'

// 团队的「额度」（`/teams/:handle/credits`）。团队从外框注入，取数在这里，画法在
// `CreditsView`。
defineOptions({ name: 'TeamCredits' })

const teamData = inject(teamDataInjectionKey, ref())
const teamId = computed(() => teamData.value?.id ?? 0)

const { usage, loading, error, load } = useCreditUsage(() => getTeamCreditUsage(teamId.value))
const personal = computed(() => !!teamData.value?.personal)
const offers = useCreditOffers(
  () => teamId.value,
  () => personal.value
)
const canManage = computed(() => ['OWNER', 'ADMIN'].includes(teamData.value?.role ?? ''))
async function claim() {
  if (await offers.claim()) await load()
}

watch(
  teamId,
  (id) => {
    if (id) {
      void load()
      void offers.load()
    }
  },
  { immediate: true }
)
</script>

<template>
  <CreditsView
    :usage="usage"
    :loading="loading"
    :error="error"
    :can-manage="canManage"
    :team-handle="teamData?.handle"
    :personal="personal"
    :beta="offers.beta.value"
    :rewards="offers.rewards.value"
    :requests="offers.requests.value"
    :offers-busy="offers.busy.value"
    :offers-error="offers.error.value"
    @retry="load"
    @request="offers.request"
    @claim="claim"
    @retry-offers="offers.load"
  />
</template>
