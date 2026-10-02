<script setup lang="ts">
import { computed, inject, ref, watch } from 'vue'

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

watch(
  teamId,
  (id) => {
    if (id) void load()
  },
  { immediate: true }
)
</script>

<template>
  <CreditsView :usage="usage" :loading="loading" :error="error" @retry="load" />
</template>
