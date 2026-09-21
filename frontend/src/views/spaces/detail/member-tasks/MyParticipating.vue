<template>
  <v-sheet flat rounded="lg" class="member-page">
    <div class="member-page__hero">
      <div>
        <h1 class="member-page__title">{{ t('spaces.detail.myJoinedContests') }}</h1>
      </div>

      <v-btn
        variant="outlined"
        rounded="lg"
        prepend-icon="mdi-compass-outline"
        :to="{ name: 'SpacesDetailTasksList', params: { spaceId } }"
      >
        {{ t('spaces.detail.memberTasks.viewAllChallenges') }}
      </v-btn>
    </div>

    <v-row dense class="mt-1">
      <template v-if="overviewLoading && !overview">
        <v-col v-for="index in 4" :key="index" cols="12" sm="6" xl="3">
          <v-skeleton-loader type="article" rounded="lg" />
        </v-col>
      </template>
      <template v-else>
        <v-col cols="12" sm="6" xl="3">
          <MemberOverviewCard
            icon="mdi-timer-sand"
            tone="warning"
            :label="t('spaces.detail.memberTasks.approval.pending')"
            :value="formatCount(overview?.pendingApprovalCount)"
            :helper="t('spaces.detail.memberTasks.participating.overview.pendingApproval')"
          />
        </v-col>
        <v-col cols="12" sm="6" xl="3">
          <MemberOverviewCard
            icon="mdi-upload-outline"
            tone="primary"
            :label="t('spaces.detail.memberTasks.completion.notSubmitted')"
            :value="formatCount(overview?.awaitingSubmissionCount)"
            :helper="t('spaces.detail.memberTasks.participating.overview.awaitingSubmission')"
          />
        </v-col>
        <v-col cols="12" sm="6" xl="3">
          <MemberOverviewCard
            icon="mdi-clipboard-text-clock-outline"
            tone="info"
            :label="t('spaces.detail.memberTasks.completion.pendingReview')"
            :value="formatCount(overview?.pendingReviewCount)"
            :helper="t('spaces.detail.memberTasks.participating.overview.pendingReview')"
          />
        </v-col>
        <v-col cols="12" sm="6" xl="3">
          <MemberOverviewCard
            icon="mdi-refresh"
            tone="warning"
            :label="t('spaces.detail.memberTasks.completion.resubmittable')"
            :value="formatCount(overview?.resubmittableCount)"
            :helper="t('spaces.detail.memberTasks.participating.overview.resubmittable')"
          />
        </v-col>
      </template>
    </v-row>

    <v-card flat rounded="lg" class="filter-card mt-5">
      <div class="filter-card__header">
        <div class="filter-card__title">{{ t('spaces.detail.memberTasks.filter') }}</div>
        <v-btn variant="text" color="primary" @click="clearFilters">{{
          t('spaces.detail.memberTasks.clearFilters')
        }}</v-btn>
      </div>

      <div class="filter-grid">
        <v-select
          v-model="approvedModel"
          autocomplete="off"
          :items="approvedItems"
          :label="t('spaces.detail.memberTasks.participating.approvalStatus')"
          density="comfortable"
          hide-details
          variant="outlined"
        />
        <v-select
          v-model="completionStatusModel"
          autocomplete="off"
          :items="completionItems"
          :label="t('spaces.detail.memberTasks.completion.status')"
          density="comfortable"
          hide-details
          variant="outlined"
        />
        <v-select
          v-model="identityTypeModel"
          autocomplete="off"
          :items="identityTypeItems"
          :label="t('spaces.detail.memberTasks.identity.label')"
          density="comfortable"
          hide-details
          variant="outlined"
        />
        <v-select
          v-model="sortByModel"
          autocomplete="off"
          :items="sortByItems"
          :label="t('spaces.detail.memberTasks.sortBy')"
          density="comfortable"
          hide-details
          variant="outlined"
        />
      </div>

      <div class="filter-grid filter-grid--secondary">
        <v-select
          v-model="sortOrderModel"
          autocomplete="off"
          :items="sortOrderItems"
          :label="t('spaces.detail.memberTasks.sortOrder')"
          density="comfortable"
          hide-details
          variant="outlined"
        />
      </div>
    </v-card>

    <div class="list-section">
      <template v-if="listLoading && !participations.length">
        <v-skeleton-loader v-for="index in 3" :key="index" type="article" rounded="lg" class="mb-4" />
      </template>

      <template v-else-if="!participations.length">
        <v-empty-state
          icon="mdi-account-check-outline"
          :title="t('spaces.detail.memberTasks.participating.emptyTitle')"
          :text="t('spaces.detail.memberTasks.participating.emptyText')"
        />
        <div class="empty-actions">
          <v-btn
            color="primary"
            rounded="lg"
            prepend-icon="mdi-compass-outline"
            :to="{ name: 'SpacesDetailTasksList', params: { spaceId } }"
          >
            {{ t('spaces.detail.memberTasks.viewAllChallenges') }}
          </v-btn>
        </div>
      </template>

      <template v-else>
        <MyParticipationCard
          v-for="item in participations"
          :key="item.participationId"
          :participation="item"
          :space-id="spaceId"
          class="mb-4"
        />
      </template>
    </div>
  </v-sheet>
</template>

<script setup lang="ts">
import type { SpaceMyParticipatingOverview, SpaceMyParticipation } from '@/network/api/spaces/types'

import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'

import MemberOverviewCard from './components/MemberOverviewCard.vue'
import MyParticipationCard from './components/MyParticipationCard.vue'
import { formatCount } from './helpers'
import {
  buildMyParticipatingApiParams,
  type MemberApproveFilter,
  type MemberCompletionFilter,
  type MemberIdentityFilter,
  type MemberSortOrder,
  type MyParticipatingSortBy,
  normalizeParticipatingQuery,
  serializeParticipatingQuery,
} from './utils'

import { SpacesApi } from '@/network/api/spaces'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()

const spaceId = computed(() => Number(route.params.spaceId))
const filters = computed(() => normalizeParticipatingQuery(route.query as Record<string, unknown>))

const overview = ref<null | SpaceMyParticipatingOverview>(null)
const participations = ref<SpaceMyParticipation[]>([])
const overviewLoading = ref(false)
const listLoading = ref(false)

const replaceFilters = async (partial: Record<string, unknown>) => {
  const next = normalizeParticipatingQuery({
    ...serializeParticipatingQuery(filters.value),
    ...partial,
  })

  await router.replace({
    query: serializeParticipatingQuery(next),
  })
}

const approvedModel = computed({
  get: () => filters.value.approved,
  set: (value: MemberApproveFilter) => {
    replaceFilters({ approved: value }).catch(() => undefined)
  },
})

const completionStatusModel = computed({
  get: () => filters.value.completionStatus,
  set: (value: MemberCompletionFilter) => {
    replaceFilters({ completionStatus: value }).catch(() => undefined)
  },
})

const identityTypeModel = computed({
  get: () => filters.value.identityType,
  set: (value: MemberIdentityFilter) => {
    replaceFilters({ identityType: value }).catch(() => undefined)
  },
})

const sortByModel = computed({
  get: () => filters.value.sortBy,
  set: (value: MyParticipatingSortBy) => {
    replaceFilters({ sortBy: value }).catch(() => undefined)
  },
})

const sortOrderModel = computed({
  get: () => filters.value.sortOrder,
  set: (value: MemberSortOrder) => {
    replaceFilters({ sortOrder: value }).catch(() => undefined)
  },
})

// 下拉项的标题跟着语言走，所以是 computed 而不是常量：写死成常量的话挂载后切语言，
// 已经画出来的筛选项还是旧语言那一份。
const approvedItems = computed(() => [
  { title: t('spaces.detail.memberTasks.all'), value: 'ALL' },
  { title: t('spaces.detail.memberTasks.approval.pending'), value: 'NONE' },
  { title: t('spaces.detail.memberTasks.approval.approved'), value: 'APPROVED' },
  { title: t('spaces.detail.memberTasks.approval.notApproved'), value: 'DISAPPROVED' },
])

const completionItems = computed(() => [
  { title: t('spaces.detail.memberTasks.all'), value: 'ALL' },
  { title: t('spaces.detail.memberTasks.completion.notSubmitted'), value: 'NOT_SUBMITTED' },
  { title: t('spaces.detail.memberTasks.completion.pendingReview'), value: 'PENDING_REVIEW' },
  { title: t('spaces.detail.memberTasks.completion.resubmittable'), value: 'REJECTED_RESUBMITTABLE' },
  { title: t('spaces.detail.memberTasks.completion.failed'), value: 'FAILED' },
  { title: t('spaces.detail.memberTasks.completion.success'), value: 'SUCCESS' },
])

const identityTypeItems = computed(() => [
  { title: t('spaces.detail.memberTasks.all'), value: 'ALL' },
  { title: t('spaces.detail.memberTasks.identity.individual'), value: 'USER' },
  { title: t('spaces.detail.memberTasks.identity.team'), value: 'TEAM' },
])

const sortByItems = computed(() => [
  { title: t('spaces.detail.memberTasks.participating.sort.joinedAt'), value: 'joinedAt' },
  { title: t('spaces.detail.memberTasks.participating.sort.deadline'), value: 'deadline' },
  { title: t('spaces.detail.memberTasks.participating.sort.latestSubmission'), value: 'latestSubmissionAt' },
  { title: t('spaces.detail.memberTasks.completion.status'), value: 'completionStatus' },
])

const sortOrderItems = computed(() => [
  { title: t('spaces.detail.memberTasks.sortDesc'), value: 'desc' },
  { title: t('spaces.detail.memberTasks.sortAsc'), value: 'asc' },
])

const loadOverview = async () => {
  overviewLoading.value = true
  try {
    const { data } = await SpacesApi.getMyParticipatingOverview(spaceId.value)
    overview.value = data
  } catch (error) {
    console.error('load my participating overview failed', error)
    toast.error(t('spaces.detail.memberTasks.participating.loadOverviewFailed'))
  } finally {
    overviewLoading.value = false
  }
}

const loadParticipations = async () => {
  listLoading.value = true
  try {
    const { data } = await SpacesApi.getMyParticipations(spaceId.value, buildMyParticipatingApiParams(filters.value))
    participations.value = data.participations
  } catch (error) {
    console.error('load my participations failed', error)
    toast.error(t('spaces.detail.memberTasks.participating.loadFailed'))
  } finally {
    listLoading.value = false
  }
}

const clearFilters = async () => {
  await router.replace({ query: {} })
}

watch(
  [spaceId, filters],
  () => {
    loadParticipations().catch(() => undefined)
  },
  { immediate: true }
)

watch(
  spaceId,
  () => {
    loadOverview().catch(() => undefined)
  },
  { immediate: true }
)
</script>

<style scoped lang="scss">
.member-page {
  padding: 20px;
}

.member-page__hero {
  display: flex;
  gap: 16px;
  justify-content: space-between;
  align-items: flex-start;
}

.member-page__title {
  margin: 0;
  font-size: 1.8rem;
  line-height: 1.25;
}

.filter-card {
  padding: 18px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.08);
}

.filter-card__header {
  display: flex;
  gap: 16px;
  justify-content: space-between;
  align-items: flex-start;
}

.filter-card__title {
  font-size: 1rem;
  font-weight: 600;
}

.filter-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-top: 16px;
}

.filter-grid--secondary {
  grid-template-columns: minmax(0, 1fr);
  max-width: 220px;
}

.list-section {
  margin-top: 22px;
}

.empty-actions {
  display: flex;
  justify-content: center;
  margin-top: 16px;
}

@media (max-width: 960px) {
  .member-page {
    padding: 16px;
  }

  .member-page__hero,
  .filter-card__header {
    flex-direction: column;
  }

  .filter-grid,
  .filter-grid--secondary {
    grid-template-columns: 1fr;
    max-width: none;
  }
}
</style>
