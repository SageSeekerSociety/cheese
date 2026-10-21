<script setup lang="ts">
import type { CreditRequest } from '@/types/creditOffers'

import { onMounted, ref } from 'vue'

import { useAdminCredits } from '@/composables/useAdminCredits'
import { useAdminInviteCodes } from '@/composables/useAdminInviteCodes'

import AdminCreditsPageView from './AdminCreditsPageView.vue'

import { getPendingCreditRequests, handleCreditRequest } from '@/api/creditOffers'

// 方案与额度（`/admin/credits`）。取数与写在 `useAdminCredits`，画法在 `AdminCreditsPageView`。
defineOptions({ name: 'AdminCreditsPage' })

const credits = useAdminCredits()
const inviteCodes = useAdminInviteCodes()
const requests = ref<CreditRequest[]>([])
const requestsBusy = ref(false)
const requestsError = ref<string | null>(null)
async function loadRequests() {
  try {
    requests.value = (await getPendingCreditRequests()).items
    requestsError.value = null
  } catch (e) {
    requestsError.value = e instanceof Error ? e.message : String(e)
  }
}
async function handleRequest(id: string, approve: boolean, confirmed: boolean, planKey?: string) {
  requestsBusy.value = true
  try {
    await handleCreditRequest(id, approve, confirmed, planKey)
    await loadRequests()
    await credits.loadTeams()
  } catch (e) {
    requestsError.value = e instanceof Error ? e.message : String(e)
  } finally {
    requestsBusy.value = false
  }
}
onMounted(loadRequests)

function dismissPlanError() {
  credits.teamPlanError.value = null
}

function dismissOverseasError() {
  credits.overseasError.value = null
}
</script>

<template>
  <AdminCreditsPageView
    :requests="requests"
    :requests-busy="requestsBusy"
    :requests-error="requestsError"
    :plans="credits.plans.value"
    :plans-loading="credits.plansLoading.value"
    :plans-error="credits.plansError.value"
    :tier-models="credits.tierModels.value"
    :plan-dialog-open="credits.planDialogOpen.value"
    :plan-editing="credits.planEditing.value"
    :plan-saving="credits.planSaving.value"
    :plan-save-error="credits.planSaveError.value"
    :query="credits.query.value"
    :plan-filter="credits.planFilter.value"
    :kind-filter="credits.kindFilter.value"
    :teams="credits.teams.value"
    :teams-loading="credits.teamsLoading.value"
    :teams-error="credits.teamsError.value"
    :searching="credits.searching.value"
    :panel-open="credits.panelOpen.value"
    :team="credits.team.value"
    :team-loading="credits.teamLoading.value"
    :team-error="credits.teamError.value"
    :history="credits.history.value"
    :history-error="credits.historyError.value"
    :team-plan-saving="credits.teamPlanSaving.value"
    :team-plan-error="credits.teamPlanError.value"
    :overseas-saving="credits.overseasSaving.value"
    :overseas-error="credits.overseasError.value"
    :grant-open="credits.grantOpen.value"
    :grant-saving="credits.grantSaving.value"
    :grant-error="credits.grantError.value"
    :invite-codes="inviteCodes.codes.value"
    :invite-codes-error="inviteCodes.loadError.value"
    :invite-codes-busy="inviteCodes.busy.value"
    :invite-codes-action-error="inviteCodes.actionError.value"
    @handle-request="handleRequest"
    @retry-requests="loadRequests"
    @retry-plans="credits.loadPlans"
    @open-plan="credits.openPlan"
    @update:plan-dialog-open="credits.planDialogOpen.value = $event"
    @save-plan="credits.savePlan"
    @delete-plan="credits.removePlan"
    @update:query="credits.setQuery"
    @update:plan-filter="credits.setPlanFilter"
    @update:kind-filter="credits.setKindFilter"
    @page="credits.setPage"
    @retry-teams="credits.loadTeams"
    @open-team="credits.openTeam"
    @update:panel-open="credits.panelOpen.value = $event"
    @retry-team="credits.retryTeam"
    @change-plan="credits.changeTeamPlan"
    @dismiss-plan-error="dismissPlanError"
    @set-overseas="credits.setTeamOverseas"
    @dismiss-overseas-error="dismissOverseasError"
    @open-grant="credits.openGrant"
    @update:grant-open="credits.grantOpen.value = $event"
    @grant="credits.grant"
    @create-invite-code="inviteCodes.create"
    @deactivate-invite-code="inviteCodes.deactivate"
    @copy-invite-code="inviteCodes.copy"
    @retry-invite-codes="inviteCodes.load"
  />
</template>
