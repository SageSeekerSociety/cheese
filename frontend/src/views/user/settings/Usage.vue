<script setup lang="ts">
import { onMounted } from 'vue'

import { useCreditOffers } from '@/composables/useCreditOffers'
import { useCreditUsage } from '@/composables/useCreditUsage'

import UsageView from './UsageView.vue'

import { getMyCreditUsage } from '@/api/creditUsage'

// 个人设置里的「芝士额度」（`/users/settings/usage`）。取数在这里，画法在 `UsageView`。
defineOptions({ name: 'UsageSettings' })

const { usage, loading, error, load } = useCreditUsage(getMyCreditUsage)
const offers = useCreditOffers()
async function claim() {
  if (await offers.claim()) await load()
}

onMounted(load)
onMounted(offers.load)
</script>

<template>
  <UsageView
    :usage="usage"
    :loading="loading"
    :error="error"
    :beta="offers.beta.value"
    :rewards="offers.rewards.value"
    :offers-busy="offers.busy.value"
    :offers-error="offers.error.value"
    @retry="load"
    @claim="claim"
    @retry-offers="offers.load"
  />
</template>
