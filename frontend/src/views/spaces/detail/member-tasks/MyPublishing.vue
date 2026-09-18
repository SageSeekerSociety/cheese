<template>
  <v-sheet flat rounded="lg" class="member-page">
    <div class="member-page__hero">
      <div>
        <h1 class="member-page__title">{{ t('spaces.detail.myPublishedContests') }}</h1>
      </div>

      <v-btn color="primary" rounded="lg" prepend-icon="mdi-plus" @click="navigateToPublishTask">
        {{ t('tasks.publish.title') }}
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
            icon="mdi-clipboard-clock-outline"
            tone="warning"
            :label="t('spaces.detail.memberTasks.publishing.overview.pendingTaskApproval')"
            :value="formatCount(overview?.pendingTaskApprovalCount)"
            :helper="t('spaces.detail.memberTasks.publishing.overview.pendingTaskApprovalHint')"
          />
        </v-col>
        <v-col cols="12" sm="6" xl="3">
          <MemberOverviewCard
            icon="mdi-account-alert-outline"
            tone="warning"
            :label="t('spaces.detail.memberTasks.publishing.overview.pendingParticipantApproval')"
            :value="formatCount(overview?.pendingParticipantApprovalCount)"
            :helper="t('spaces.detail.memberTasks.publishing.overview.pendingParticipantApprovalHint')"
          />
        </v-col>
        <v-col cols="12" sm="6" xl="3">
          <MemberOverviewCard
            icon="mdi-clipboard-text-clock-outline"
            tone="info"
            :label="t('spaces.detail.memberTasks.publishing.overview.pendingReviewSubmission')"
            :value="formatCount(overview?.pendingReviewCount)"
            :helper="t('spaces.detail.memberTasks.publishing.overview.pendingReviewSubmissionHint')"
          />
        </v-col>
        <v-col cols="12" sm="6" xl="3">
          <MemberOverviewCard
            icon="mdi-trophy-outline"
            tone="success"
            :label="t('spaces.detail.memberTasks.publishing.overview.successfulParticipants')"
            :value="formatCount(overview?.successfulParticipantCount)"
            :helper="t('spaces.detail.memberTasks.publishing.overview.successfulParticipantsHint')"
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
          v-model="categoryIdModel"
          autocomplete="off"
          :items="categoryItems"
          :label="t('spaces.detail.memberTasks.publishing.category')"
          density="comfortable"
          hide-details
          variant="outlined"
        />
        <v-select
          v-model="approvedModel"
          autocomplete="off"
          :items="approvedItems"
          :label="t('spaces.detail.memberTasks.publishing.approvalStatus')"
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

      <div class="filter-chips">
        <v-chip
          :variant="filters.hasPendingParticipantApproval ? 'flat' : 'outlined'"
          color="warning"
          @click="togglePendingParticipantApproval"
        >
          {{ t('spaces.detail.memberTasks.publishing.overview.pendingParticipantApproval') }}
        </v-chip>
        <v-chip :variant="filters.hasPendingReview ? 'flat' : 'outlined'" color="info" @click="togglePendingReview">
          {{ t('spaces.detail.memberTasks.publishing.overview.pendingReviewSubmission') }}
        </v-chip>
      </div>
    </v-card>

    <div class="list-section">
      <template v-if="listLoading && !tasks.length">
        <v-skeleton-loader v-for="index in 3" :key="index" type="article" rounded="lg" class="mb-4" />
      </template>

      <template v-else-if="!tasks.length">
        <v-empty-state
          icon="mdi-pencil-box-multiple-outline"
          :title="t('spaces.detail.memberTasks.publishing.emptyTitle')"
          :text="t('spaces.detail.memberTasks.publishing.emptyText')"
        />
        <div class="empty-actions">
          <v-btn color="primary" rounded="lg" prepend-icon="mdi-plus" @click="navigateToPublishTask">
            {{ t('spaces.detail.memberTasks.publishing.goPublish') }}
          </v-btn>
        </div>
      </template>

      <template v-else>
        <MyPublishedTaskCard v-for="task in tasks" :key="task.taskId" :task="task" :space-id="spaceId" class="mb-4" />
      </template>
    </div>
  </v-sheet>
</template>

<script setup lang="ts">
import type { SpaceMyPublishedTask, SpaceMyPublishingOverview } from '@/network/api/spaces/types'

import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'
import { storeToRefs } from 'pinia'

import MemberOverviewCard from './components/MemberOverviewCard.vue'
import MyPublishedTaskCard from './components/MyPublishedTaskCard.vue'
import { formatCount } from './helpers'
import {
  buildMyPublishingApiParams,
  type MemberApproveFilter,
  type MemberSortOrder,
  type MyPublishingSortBy,
  normalizePublishingQuery,
  serializePublishingQuery,
} from './utils'

import { SpacesApi } from '@/network/api/spaces'
import { useSpaceStore } from '@/stores/space'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()

const spaceStore = useSpaceStore()
const { currentSpaceId, currentSpace, categories } = storeToRefs(spaceStore)
const { fetchCategories } = spaceStore

const spaceId = computed(() => Number(route.params.spaceId))
const filters = computed(() => normalizePublishingQuery(route.query as Record<string, unknown>))

const overview = ref<null | SpaceMyPublishingOverview>(null)
const tasks = ref<SpaceMyPublishedTask[]>([])
const overviewLoading = ref(false)
const listLoading = ref(false)

const replaceFilters = async (partial: Record<string, unknown>) => {
  const next = normalizePublishingQuery({
    ...serializePublishingQuery(filters.value),
    ...partial,
  })

  await router.replace({
    query: serializePublishingQuery(next),
  })
}

const categoryIdModel = computed({
  get: () => filters.value.categoryId ?? null,
  set: (value: null | number) => {
    replaceFilters({ categoryId: value ?? undefined }).catch(() => undefined)
  },
})

const approvedModel = computed({
  get: () => filters.value.approved,
  set: (value: MemberApproveFilter) => {
    replaceFilters({ approved: value }).catch(() => undefined)
  },
})

const sortByModel = computed({
  get: () => filters.value.sortBy,
  set: (value: MyPublishingSortBy) => {
    replaceFilters({ sortBy: value }).catch(() => undefined)
  },
})

const sortOrderModel = computed({
  get: () => filters.value.sortOrder,
  set: (value: MemberSortOrder) => {
    replaceFilters({ sortOrder: value }).catch(() => undefined)
  },
})

const categoryItems = computed(() => [
  { title: t('spaces.detail.allCategories'), value: null },
  ...categories.value.filter((item) => !item.archivedAt).map((item) => ({ title: item.name, value: item.id })),
])

// 和 MyParticipating 一样，下拉项的标题跟着语言走，所以这里是 computed。
const approvedItems = computed(() => [
  { title: t('spaces.detail.memberTasks.all'), value: 'ALL' },
  { title: t('spaces.detail.memberTasks.approval.pending'), value: 'NONE' },
  { title: t('spaces.detail.memberTasks.approval.approved'), value: 'APPROVED' },
  { title: t('spaces.detail.memberTasks.approval.notApproved'), value: 'DISAPPROVED' },
])

const sortByItems = computed(() => [
  { title: t('spaces.detail.tasks.sortOptions.latestPublished'), value: 'publishedAt' },
  { title: t('spaces.detail.tasks.sortOptions.latestPublished'), value: 'createdAt' },
  { title: t('spaces.detail.memberTasks.publishing.sort.participantCount'), value: 'participantCount' },
  { title: t('spaces.detail.memberTasks.publishing.sort.pendingReview'), value: 'pendingReviewCount' },
  { title: t('spaces.detail.memberTasks.publishing.sort.successRate'), value: 'successRate' },
])

const sortOrderItems = computed(() => [
  { title: t('spaces.detail.memberTasks.sortDesc'), value: 'desc' },
  { title: t('spaces.detail.memberTasks.sortAsc'), value: 'asc' },
])

const loadOverview = async () => {
  overviewLoading.value = true
  try {
    const { data } = await SpacesApi.getMyPublishingOverview(spaceId.value)
    overview.value = data
  } catch (error) {
    console.error('load my publishing overview failed', error)
    toast.error(t('spaces.detail.memberTasks.publishing.loadOverviewFailed'))
  } finally {
    overviewLoading.value = false
  }
}

const loadTasks = async () => {
  listLoading.value = true
  try {
    const { data } = await SpacesApi.getMyPublishedTasks(spaceId.value, buildMyPublishingApiParams(filters.value))
    tasks.value = data.tasks
  } catch (error) {
    console.error('load my publishing tasks failed', error)
    toast.error(t('spaces.detail.memberTasks.publishing.loadFailed'))
  } finally {
    listLoading.value = false
  }
}

const clearFilters = async () => {
  await router.replace({ query: {} })
}

const togglePendingParticipantApproval = async () => {
  await replaceFilters({
    hasPendingParticipantApproval: filters.value.hasPendingParticipantApproval ? undefined : true,
  })
}

const togglePendingReview = async () => {
  await replaceFilters({
    hasPendingReview: filters.value.hasPendingReview ? undefined : true,
  })
}

const navigateToPublishTask = async () => {
  try {
    if (currentSpace.value) {
      const taskTemplates = JSON.parse(currentSpace.value.taskTemplates || '[]')
      const query: Record<string, string> = {}
      if (filters.value.categoryId) {
        query.categoryId = String(filters.value.categoryId)
      }

      if (taskTemplates.length > 0) {
        await router.push({
          name: 'SpacesDetailSelectTemplate',
          params: { spaceId: route.params.spaceId },
          query,
        })
      } else {
        await router.push({
          name: 'SpacesDetailPublishTask',
          params: { spaceId: route.params.spaceId },
          query,
        })
      }
    }
  } catch (error) {
    console.error('navigate to publish task failed', error)
    await router.push({ name: 'SpacesDetailPublishTask', params: { spaceId: route.params.spaceId } })
  }
}

watch(
  currentSpaceId,
  (value) => {
    if (value) {
      fetchCategories().catch(() => undefined)
    }
  },
  { immediate: true }
)

watch(
  [spaceId, filters],
  () => {
    loadTasks().catch(() => undefined)
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

onMounted(() => {
  fetchCategories().catch(() => undefined)
})
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

.filter-chips {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 14px;
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

  .filter-grid {
    grid-template-columns: 1fr;
  }
}
</style>
