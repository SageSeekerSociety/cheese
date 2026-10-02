<script setup lang="ts">
import { useAdminCredits } from '@/composables/useAdminCredits'

import AdminCreditsPageView from './AdminCreditsPageView.vue'

// 方案与额度（`/admin/credits`）。取数与写在 `useAdminCredits`，画法在 `AdminCreditsPageView`。
defineOptions({ name: 'AdminCreditsPage' })

const credits = useAdminCredits()

function dismissPlanError() {
  credits.teamPlanError.value = null
}
</script>

<template>
  <AdminCreditsPageView
    :plans="credits.plans.value"
    :plans-loading="credits.plansLoading.value"
    :plans-error="credits.plansError.value"
    :tier-models="credits.tierModels.value"
    :plan-dialog-open="credits.planDialogOpen.value"
    :plan-editing="credits.planEditing.value"
    :plan-saving="credits.planSaving.value"
    :plan-save-error="credits.planSaveError.value"
    :query="credits.query.value"
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
    :grant-open="credits.grantOpen.value"
    :grant-saving="credits.grantSaving.value"
    :grant-error="credits.grantError.value"
    @retry-plans="credits.loadPlans"
    @open-plan="credits.openPlan"
    @update:plan-dialog-open="credits.planDialogOpen.value = $event"
    @save-plan="credits.savePlan"
    @update:query="credits.setQuery"
    @page="credits.setPage"
    @retry-teams="credits.loadTeams"
    @open-team="credits.openTeam"
    @update:panel-open="credits.panelOpen.value = $event"
    @retry-team="credits.retryTeam"
    @change-plan="credits.changeTeamPlan"
    @dismiss-plan-error="dismissPlanError"
    @open-grant="credits.openGrant"
    @update:grant-open="credits.grantOpen.value = $event"
    @grant="credits.grant"
  />
</template>
