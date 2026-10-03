<script setup lang="ts">
import type {
  CreditAudit,
  CreditTeamDetail,
  CreditTeamPage,
  CreditTeamRow,
  GrantInput,
  ModelTier,
  Plan,
  PlanInput,
  TeamKind,
} from '@/lib/adminCredits'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminPage from '@/components/admin/AdminPage.vue'
import AdminCreditTeamPanel from '@/components/admin/credits/AdminCreditTeamPanel.vue'
import AdminCreditTeamsTable from '@/components/admin/credits/AdminCreditTeamsTable.vue'
import AdminGrantDialog from '@/components/admin/credits/AdminGrantDialog.vue'
import AdminPlanDialog from '@/components/admin/credits/AdminPlanDialog.vue'
import AdminPlansTable from '@/components/admin/credits/AdminPlansTable.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import { teamTitle } from '@/lib/adminCredits'

// 方案与额度（`/admin/credits`）的画法：方案一览、团队一览，点开一个团队是右边的面板。
// 取数与写在 `useAdminCredits`，这里只吃数据、往上报动作。
defineOptions({ name: 'AdminCreditsPageView' })

const props = defineProps<{
  plans: Plan[] | null
  plansLoading: boolean
  plansError: string | null
  tierModels: Partial<Record<ModelTier, string[]>>
  planDialogOpen: boolean
  planEditing: Plan | null
  planSaving: boolean
  planSaveError: string | null
  query: string
  planFilter: string | null
  kindFilter: TeamKind | null
  teams: CreditTeamPage | null
  teamsLoading: boolean
  teamsError: string | null
  searching: boolean
  panelOpen: boolean
  team: CreditTeamDetail | null
  teamLoading: boolean
  teamError: string | null
  history: CreditAudit[] | null
  historyError: string | null
  teamPlanSaving: boolean
  teamPlanError: string | null
  grantOpen: boolean
  grantSaving: boolean
  grantError: string | null
}>()

const emit = defineEmits<{
  'retry-plans': []
  'open-plan': [plan: Plan | null]
  'update:planDialogOpen': [open: boolean]
  'save-plan': [input: PlanInput]
  'delete-plan': []
  'update:query': [value: string]
  'update:planFilter': [value: string | null]
  'update:kindFilter': [value: TeamKind | null]
  page: [page: number]
  'retry-teams': []
  'open-team': [row: CreditTeamRow]
  'update:panelOpen': [open: boolean]
  'retry-team': []
  'change-plan': [planKey: string]
  'dismiss-plan-error': []
  'open-grant': []
  'update:grantOpen': [open: boolean]
  grant: [input: GrantInput]
}>()

const { t } = useI18n()

/** 筛选的「全部」是空值：服务端不带这一项就是不筛。 */
const planOptions = computed(() => [
  { value: null, title: t('credits.teams.all') },
  ...(props.plans ?? []).map((plan) => ({ value: plan.key, title: plan.name })),
])
const kindOptions = computed(() => [
  { value: null, title: t('credits.teams.all') },
  { value: 'personal', title: t('credits.teams.kindPersonal') },
  { value: 'team', title: t('credits.teams.kindTeam') },
])
</script>

<template>
  <AdminPage :title="t('navigation.admin.credits')">
    <div class="acr__body admin-page__body">
      <section class="acr__section">
        <div class="acr__sectionhead">
          <h2 class="acr__h t-title">{{ t('credits.plans.title') }}</h2>
          <BaseButton kind="primary" size="sm" prepend-icon="mdi-plus" @click="emit('open-plan', null)">
            {{ t('credits.plans.add') }}
          </BaseButton>
        </div>
        <AdminPlansTable
          :plans="props.plans"
          :loading="props.plansLoading"
          :error="props.plansError"
          @edit="emit('open-plan', $event)"
          @retry="emit('retry-plans')"
        />
      </section>

      <section class="acr__section">
        <div class="acr__sectionhead">
          <h2 class="acr__h t-title">{{ t('credits.teams.title') }}</h2>
          <div class="acr__filters">
            <v-select
              autocomplete="off"
              :model-value="props.planFilter"
              :items="planOptions"
              item-title="title"
              item-value="value"
              variant="outlined"
              density="compact"
              :label="t('credits.teams.filterPlan')"
              hide-details
              class="acr__filter"
              @update:model-value="emit('update:planFilter', $event)"
            />
            <v-select
              autocomplete="off"
              :model-value="props.kindFilter"
              :items="kindOptions"
              item-title="title"
              item-value="value"
              variant="outlined"
              density="compact"
              :label="t('credits.teams.filterKind')"
              hide-details
              class="acr__filter"
              @update:model-value="emit('update:kindFilter', $event)"
            />
            <v-text-field
              :model-value="props.query"
              autocomplete="off"
              type="search"
              variant="outlined"
              density="compact"
              prepend-inner-icon="mdi-magnify"
              :placeholder="t('credits.teams.search')"
              :aria-label="t('credits.teams.search')"
              hide-details
              class="acr__search"
              @update:model-value="emit('update:query', $event)"
            />
          </div>
        </div>
        <AdminCreditTeamsTable
          :page="props.teams"
          :plans="props.plans ?? []"
          :loading="props.teamsLoading"
          :error="props.teamsError"
          :searching="props.searching"
          @open="emit('open-team', $event)"
          @page="emit('page', $event)"
          @retry="emit('retry-teams')"
        />
      </section>
    </div>

    <AdminPlanDialog
      :model-value="props.planDialogOpen"
      :plan="props.planEditing"
      :saving="props.planSaving"
      :error="props.planSaveError"
      :tier-models="props.tierModels"
      @update:model-value="emit('update:planDialogOpen', $event)"
      @submit="emit('save-plan', $event)"
      @delete="emit('delete-plan')"
    />

    <AdminCreditTeamPanel
      :model-value="props.panelOpen"
      :team="props.team"
      :loading="props.teamLoading"
      :error="props.teamError"
      :plans="props.plans ?? []"
      :plan-saving="props.teamPlanSaving"
      :plan-error="props.teamPlanError"
      :history="props.history"
      :history-error="props.historyError"
      @update:model-value="emit('update:panelOpen', $event)"
      @change-plan="emit('change-plan', $event)"
      @dismiss-plan-error="emit('dismiss-plan-error')"
      @grant="emit('open-grant')"
      @retry="emit('retry-team')"
    />

    <AdminGrantDialog
      :model-value="props.grantOpen"
      :team-name="props.team ? teamTitle(props.team) : ''"
      :saving="props.grantSaving"
      :error="props.grantError"
      @update:model-value="emit('update:grantOpen', $event)"
      @submit="emit('grant', $event)"
    />
  </AdminPage>
</template>

<style scoped>
.acr__body {
  gap: 32px;
}

.acr__section {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.acr__sectionhead {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 40px;
}

.acr__h {
  margin: 0;
  color: var(--ink);
}

.acr__filters {
  display: flex;
  flex: 1 1 auto;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  min-width: 0;
}

.acr__filter {
  flex: 0 0 140px;
}

.acr__search {
  flex: 0 1 260px;
}
</style>
