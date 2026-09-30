<template>
  <SecondaryNavigation>
    <!-- 侧栏头：‹ 回到首页，后面是这个空间。和右边的标题行等高，底线连成一条。 -->
    <router-link :to="{ name: 'inbox' }" class="sidebar-header space-head" :aria-label="t('spaces.sidebar.back')">
      <v-icon size="18" class="space-head__back">mdi-chevron-left</v-icon>
      <v-avatar size="22" rounded="sm" :image="getAvatarUrl(space?.avatarId)" />
      <span class="space-head__name">{{ space?.name }}</span>
    </router-link>

    <!-- 手机上侧栏是抽屉，每一行至少 44px 高，手指点得准；桌面上用紧凑行。 -->
    <v-list nav :density="mdAndUp ? 'compact' : 'default'" :lines="false" class="space-nav pa-2" bg-color="transparent">
      <v-list-item
        rounded="lg"
        prepend-icon="mdi-bullhorn-outline"
        :to="{ name: 'SpacesAnnouncements', params: { spaceId } }"
        :title="t('spaces.sidebar.announcements')"
      />

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

      <template v-if="isManager">
        <v-list-subheader>{{ t('spaces.sidebar.manage') }}</v-list-subheader>
        <v-list-item
          rounded="lg"
          prepend-icon="mdi-check-decagram-outline"
          :to="{ name: 'SpacesDetailAuditTasks', params: { spaceId } }"
          :title="t('spaces.sidebar.audit')"
        >
          <template v-if="pendingAuditCount" #append>
            <span class="space-nav__count">{{ pendingAuditCount > 99 ? '99+' : pendingAuditCount }}</span>
          </template>
        </v-list-item>
        <v-list-item
          rounded="lg"
          prepend-icon="mdi-account-multiple-outline"
          :to="{ name: 'SpacesDetailMembers', params: { spaceId } }"
          :title="t('spaces.sidebar.members')"
        />
        <v-list-item
          rounded="lg"
          prepend-icon="mdi-chart-line"
          :to="{ name: 'SpacesDetailAnalytics', params: { spaceId } }"
          :title="t('spaces.sidebar.analytics')"
        />
        <v-list-item
          rounded="lg"
          prepend-icon="mdi-cog-outline"
          :to="{ name: 'SpacesDetailSettings', params: { spaceId } }"
          :active="isSettingsActive"
          :title="t('spaces.sidebar.settings')"
        />
      </template>
    </v-list>
  </SecondaryNavigation>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { useDisplay } from 'vuetify'
import { storeToRefs } from 'pinia'

import { getAvatarUrl } from '@/utils/materials'

import SecondaryNavigation from '@/components/common/Navigation/SecondaryNavigation.vue'
import { useSpaceStore } from '@/stores/space'

const { t } = useI18n()
const route = useRoute()
const spaceStore = useSpaceStore()
const { currentSpace: space, categories, isManager, pendingAuditCount } = storeToRefs(spaceStore)
const { mdAndUp } = useDisplay()

/** 侧栏里的分类：未归档的，按显示顺序。 */
const activeCategories = computed(() =>
  categories.value.filter((category) => !category.archivedAt).sort((a, b) => a.displayOrder - b.displayOrder)
)

const spaceId = computed(() => Number(route.params.spaceId))
/** 设置那一格：五个分栏和模板的新建、编辑页都算在里面。 */
const isSettingsActive = computed(() => route.path.startsWith(`/spaces/${spaceId.value}/manage/settings`))
const isUnderTasksSection = computed(() => route.matched.some((record) => record.name === 'SpacesDetailTasks'))

/** 题目列表那几格（全部 / 各分类）亮不亮：只看地址里的分类。 */
function isTasksLinkActive(query: Record<string, string> = {}): boolean {
  if (!isUnderTasksSection.value) return false
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

/* 待审核的件数：属于「等你处理」那一族，和首页待办的角标同色。 */
.space-nav__count {
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: var(--radius-pill);
  background: var(--warn);
  color: var(--inverse-surface);
  font-size: 12px;
  font-weight: 600;
  line-height: 18px;
  text-align: center;
}

.space-nav .v-list-item--active :deep(.v-list-item-title) {
  color: var(--ink);
  font-weight: 600;
}
</style>
