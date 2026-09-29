<template>
  <SecondaryNavigation>
    <!-- 侧栏头：‹ 回到首页，后面是这个空间。和右边的标题行等高，底线连成一条。 -->
    <router-link :to="{ name: 'inbox' }" class="sidebar-header space-head" :aria-label="t('spaces.sidebar.back')">
      <v-icon size="18" class="space-head__back">mdi-chevron-left</v-icon>
      <v-avatar size="22" rounded="sm" :image="getAvatarUrl(space?.avatarId)" />
      <span class="space-head__name">{{ space?.name }}</span>
    </router-link>

    <v-list nav density="compact" :lines="false" class="space-nav pa-2" bg-color="transparent">
      <v-list-subheader>{{ t('spaces.sidebar.tasks') }}</v-list-subheader>
      <v-list-item
        rounded="lg"
        prepend-icon="mdi-view-grid-outline"
        :to="{ name: 'SpacesDetailTasksList', params: { spaceId } }"
        :active="isTasksLinkActive()"
        :title="t('spaces.detail.allContests')"
      />
      <v-list-item
        v-for="category in activeCategories"
        :key="`category-${category.id}`"
        rounded="lg"
        prepend-icon="mdi-shape-outline"
        :to="{ name: 'SpacesDetailTasksList', params: { spaceId }, query: { category: category.id } }"
        :active="isTasksLinkActive({ category: category.id.toString() })"
        :title="category.name"
      />
      <v-list-item
        rounded="lg"
        prepend-icon="mdi-pencil-box-multiple-outline"
        :to="{ name: 'SpacesDetailMyPublishing', params: { spaceId } }"
        :active="isMyPublishingLinkActive"
        :title="t('spaces.detail.myPublishedContests')"
      />
      <v-list-item
        rounded="lg"
        prepend-icon="mdi-account-check-outline"
        :to="{ name: 'SpacesDetailMyParticipating', params: { spaceId } }"
        :active="isMyParticipatingLinkActive"
        :title="t('spaces.detail.myJoinedContests')"
      />
      <v-list-item
        rounded="lg"
        prepend-icon="mdi-bullhorn-outline"
        :to="{ name: 'SpacesAnnouncements', params: { spaceId } }"
        :title="t('spaces.detail.announcements')"
      />

      <template v-if="isManager">
        <v-list-subheader>{{ t('spaces.sidebar.manage') }}</v-list-subheader>
        <v-list-item
          v-for="cell in manageCells"
          :key="cell.route"
          rounded="lg"
          :prepend-icon="cell.icon"
          :to="{ name: cell.route, params: { spaceId } }"
          :title="t(cell.label)"
        />
        <v-list-item
          rounded="lg"
          prepend-icon="mdi-pencil-outline"
          :title="t('spaces.detail.editInfo')"
          @click="openEditProfile"
        />
      </template>
    </v-list>
  </SecondaryNavigation>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { storeToRefs } from 'pinia'

import { getAvatarUrl } from '@/utils/materials'

import SecondaryNavigation from '@/components/common/Navigation/SecondaryNavigation.vue'
import { useSpaceStore } from '@/stores/space'

const { t } = useI18n()
const route = useRoute()
const spaceStore = useSpaceStore()
const { openEditProfile } = spaceStore
const { currentSpace: space, categories, isManager } = storeToRefs(spaceStore)

/** 管理那一段：只有所有者与管理员看得见（接口对别人也不开）。 */
const manageCells = [
  { route: 'SpacesDetailAuditTasks', icon: 'mdi-check-decagram-outline', label: 'spaces.detail.auditContests' },
  { route: 'SpacesDetailMembers', icon: 'mdi-account-multiple-outline', label: 'spaces.members.title' },
  { route: 'SpacesDetailAnalytics', icon: 'mdi-chart-line', label: 'spaces.detail.analytics.title' },
  {
    route: 'SpacesDetailManageTemplates',
    icon: 'mdi-file-document-outline',
    label: 'spaces.detail.manageTemplates.title',
  },
  { route: 'SpacesDetailManageCategories', icon: 'mdi-shape-outline', label: 'spaces.detail.manageCategories.title' },
  { route: 'SpacesDetailManageTopics', icon: 'mdi-tag-multiple-outline', label: 'spaces.detail.manageTopics.title' },
  { route: 'SpacesDetailManageDomainGroups', icon: 'mdi-web', label: 'spaces.domainGroups.title' },
  {
    route: 'SpacesDetailManageInviteCodes',
    icon: 'mdi-ticket-confirmation-outline',
    label: 'spaces.inviteCodes.title',
  },
]

/** 侧栏里的分类：未归档的，按显示顺序。 */
const activeCategories = computed(() =>
  categories.value.filter((category) => !category.archivedAt).sort((a, b) => a.displayOrder - b.displayOrder)
)

const spaceId = computed(() => Number(route.params.spaceId))
const taskOrigin = computed(() => (typeof route.query.from === 'string' ? route.query.from : undefined))
const isUnderTasksSection = computed(() => route.matched.some((record) => record.name === 'SpacesDetailTasks'))

const isMyPublishingLinkActive = computed(
  () => isUnderTasksSection.value && (route.name === 'SpacesDetailMyPublishing' || taskOrigin.value === 'my-publishing')
)

const isMyParticipatingLinkActive = computed(
  () =>
    isUnderTasksSection.value &&
    (route.name === 'SpacesDetailMyParticipating' || taskOrigin.value === 'my-participating')
)

/** 题目列表那几格（全部 / 各分类）亮不亮：只看地址里的分类，从「我的」点进来的题目详情不算。 */
function isTasksLinkActive(query: Record<string, string> = {}): boolean {
  if (!isUnderTasksSection.value) return false
  if (taskOrigin.value === 'my-publishing' || taskOrigin.value === 'my-participating') return false
  if (route.name === 'SpacesDetailMyPublishing' || route.name === 'SpacesDetailMyParticipating') return false
  const current = typeof route.query.category === 'string' ? route.query.category : undefined
  return current === query.category
}
</script>

<style scoped>
.space-head {
  justify-content: flex-start;
  color: var(--ink);
  text-decoration: none;
}

.space-head:hover {
  background: var(--fill-2);
}

.space-head__back {
  color: var(--muted);
}

.space-head__name {
  overflow: hidden;
  font-size: 15px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 选中与悬停都是中性色，同首页侧栏：琥珀在导航里只留给左栏那一格「当前在哪」。 */
.space-nav .v-list-item {
  transition: background-color var(--dur-quick) var(--ease-standard);
}

.space-nav .v-list-item:hover {
  background: var(--fill-2);
}

.space-nav .v-list-item--active,
.space-nav .v-list-item--active:hover {
  background: var(--line-2);
}

.space-nav .v-list-item--active :deep(.v-list-item__overlay) {
  opacity: 0 !important;
}

.space-nav .v-list-item--active :deep(.v-list-item-title) {
  color: var(--ink);
  font-weight: 600;
}
</style>
