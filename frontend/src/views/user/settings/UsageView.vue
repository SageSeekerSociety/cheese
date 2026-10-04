<script setup lang="ts">
import type { CreditUsage, UsageTeam } from '@/lib/creditUsage'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminEmptyState from '@/components/admin/AdminEmptyState.vue'
import UsageDailyChart from '@/components/usage/UsageDailyChart.vue'
import UsagePackList from '@/components/usage/UsagePackList.vue'
import UsagePeriodCard from '@/components/usage/UsagePeriodCard.vue'
import UsageShareList from '@/components/usage/UsageShareList.vue'
import UsageTeamList from '@/components/usage/UsageTeamList.vue'
import { fmtMonth } from '@/lib/creditUsage'

// 个人设置里的「芝士额度」：本月用了多少（按协作、问答、写作、算力分）、其他额度、每天用量、
// 我名下的项目各占多少、我所在的团队还剩多少。
defineOptions({ name: 'UsageSettingsView' })

const props = defineProps<{
  usage: CreditUsage | null
  loading: boolean
  error: string | null
}>()

const emit = defineEmits<{ retry: [] }>()

const { t, locale } = useI18n()

const month = computed(() => (props.usage ? fmtMonth(props.usage.days, locale.value) : ''))
const projects = computed(() =>
  (props.usage?.projects ?? []).map((p) => ({
    id: p.id,
    name: p.name ?? t('usage.projects.unnamed'),
    credits: p.credits,
    to: { name: 'workspace-project', params: { projectId: p.id } },
  }))
)

/** 方案之外的额度还剩多少点。 */
const otherCredits = computed(() => (props.usage?.packs ?? []).reduce((sum, p) => sum + p.credits_remaining, 0))

const teamLink = (team: UsageTeam) => ({ name: 'TeamsDetailCredits', params: { handle: team.handle } })
</script>

<template>
  <div class="settings-page">
    <header>
      <h1 class="t-page-title">{{ t('usage.title') }}</h1>
    </header>

    <v-skeleton-loader v-if="loading && !usage" type="article, image" />

    <AdminEmptyState
      v-else-if="error && !usage"
      tone="error"
      :title="t('usage.loadFailed')"
      :desc="error"
      :action="t('usage.retry')"
      @action="emit('retry')"
    />

    <template v-else-if="usage">
      <section class="settings-card usv__card">
        <UsagePeriodCard
          :period="usage.period"
          :plan="usage.plan"
          :other-credits="otherCredits"
          :month="month"
          :lines="usage.lines"
          :windows="usage.windows"
        />
      </section>

      <section v-if="usage.packs.length" class="settings-card usv__card">
        <UsagePackList :packs="usage.packs" />
      </section>

      <section class="settings-card usv__card">
        <UsageDailyChart :days="usage.days" stacked />
      </section>

      <section class="settings-card usv__card">
        <UsageShareList :title="t('usage.projects.mine')" :items="projects" />
      </section>

      <section v-if="usage.teams?.length" class="settings-card usv__card">
        <UsageTeamList :teams="usage.teams" :link-of="teamLink" />
      </section>
    </template>
  </div>
</template>

<style scoped src="@/styles/settings-card.css"></style>

<style scoped>
.usv__card {
  padding: 20px 24px;
}

@media (max-width: 700px) {
  .usv__card {
    padding: 16px;
  }
}
</style>
