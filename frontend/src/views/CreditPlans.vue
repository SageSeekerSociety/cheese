<script setup lang="ts">
import type { UsagePlan } from '@/lib/creditUsage'
import type { Team } from '@/types'
import type { BetaCredits, CreditRequest, PublishedCreditPlan } from '@/types/creditOffers'

import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { createCreditRequest, getBetaCredits, getCreditRequests, getPublishedCreditPlans } from '@/api/creditOffers'
import { getTeamCreditUsage } from '@/api/creditUsage'
import { TeamsApi } from '@/api/teams'
import { t } from '@/i18n'
import CreditPlansView from '@/views/CreditPlansView.vue'

const route = useRoute()
const router = useRouter()
const plans = ref<PublishedCreditPlan[]>([])
const teams = ref<Team[]>([])
const currentPlan = ref<UsagePlan | null>(null)
const beta = ref<BetaCredits | null>(null)
const requests = ref<CreditRequest[]>([])
const loading = ref(false)
const busy = ref(false)
const error = ref<string | null>(null)
const teamHandle = computed(() => (typeof route.query.team === 'string' ? route.query.team : ''))
const team = computed(() =>
  teams.value.find((item) => (teamHandle.value ? item.handle === teamHandle.value : item.personal))
)
const personal = computed(() => !!team.value?.personal)
const canManage = computed(() => ['OWNER', 'ADMIN'].includes(team.value?.role ?? ''))
let version = 0
async function load() {
  const current = ++version
  loading.value = true
  currentPlan.value = null
  requests.value = []
  error.value = null
  try {
    const [catalog, membership, participation] = await Promise.all([
      getPublishedCreditPlans(),
      TeamsApi.getMyTeams(),
      getBetaCredits(),
    ])
    if (current !== version) return
    plans.value = catalog.plans
    teams.value = membership.teams
    beta.value = participation
    const selected = teams.value.find((item) => (teamHandle.value ? item.handle === teamHandle.value : item.personal))
    if (!selected) throw new Error(t('usage.plans.unavailableTeam'))
    const [usage, pending] = await Promise.all([getTeamCreditUsage(selected.id), getCreditRequests(selected.id)])
    if (current !== version) return
    currentPlan.value = usage.plan
    requests.value = pending.items
  } catch (e) {
    if (current === version) error.value = e instanceof Error ? e.message : String(e)
  } finally {
    if (current === version) loading.value = false
  }
}
async function requestPlan() {
  const selected = team.value
  if (!selected || personal.value || !canManage.value || busy.value || loading.value || error.value) return
  busy.value = true
  error.value = null
  try {
    await createCreditRequest(selected.id, 'team_plan')
    await load()
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    busy.value = false
  }
}
function selectTeam(handle: string) {
  void router.replace({ name: 'CreditPlans', query: handle ? { team: handle } : {} })
}
watch(teamHandle, load, { immediate: true })
</script>

<template>
  <CreditPlansView
    :plans="plans"
    :teams="teams"
    :team-handle="team?.handle ?? teamHandle"
    :personal="personal"
    :current-plan="currentPlan"
    :beta="beta"
    :requests="requests"
    :can-manage="canManage"
    :loading="loading"
    :busy="busy"
    :error="error"
    @select="selectTeam"
    @request="requestPlan"
    @retry="load"
  />
</template>
