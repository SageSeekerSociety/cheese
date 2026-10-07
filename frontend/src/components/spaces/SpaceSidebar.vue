<template>
  <SecondaryNavigation>
    <!-- 侧栏头：‹ 回到首页，后面是这个空间。和右边的标题行等高，底线连成一条。 -->
    <router-link :to="{ name: 'inbox' }" class="sidebar-header space-head" :aria-label="t('spaces.sidebar.back')">
      <v-icon size="18" class="space-head__back">mdi-chevron-left</v-icon>
      <UserAvatar kind="org" :avatar="getAvatarUrl(space?.avatarId)" :name="space?.name" size="20" />
      <span class="space-head__name t-title" data-user-content>{{ space?.name }}</span>
    </router-link>

    <!-- 手机上侧栏是抽屉，每一行至少 44px 高，手指点得准；桌面上用紧凑行。 -->
    <v-list nav :density="mdAndUp ? 'compact' : 'default'" :lines="false" class="side-nav pa-2" bg-color="transparent">
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
        data-user-content
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
            <span class="side-nav__count">{{ pendingAuditCount > 99 ? '99+' : pendingAuditCount }}</span>
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
import { useDisplay } from 'vuetify'
import { storeToRefs } from 'pinia'

import { getAvatarUrl } from '@/utils/materials'

import { useNavigation } from '@/composables/useNavigation'

import SecondaryNavigation from '@/components/common/Navigation/SecondaryNavigation.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import { useSpaceStore } from '@/stores/space'

const { t } = useI18n()
const nav = useNavigation()
const spaceStore = useSpaceStore()
const { currentSpace: space, categories, isManager, pendingAuditCount } = storeToRefs(spaceStore)
const { mdAndUp } = useDisplay()

/** 侧栏里的分类：未归档的，按显示顺序。 */
const activeCategories = computed(() =>
  categories.value.filter((category) => !category.archivedAt).sort((a, b) => a.displayOrder - b.displayOrder)
)

const spaceId = computed(() => Number(nav?.route?.params?.spaceId))
/** 设置那一格：五个分栏和模板的新建、编辑页都算在里面。 */
const isSettingsActive = computed(
  () => nav?.route?.path?.startsWith(`/spaces/${spaceId.value}/manage/settings`) ?? false
)
const isUnderTasksSection = computed(
  () => nav?.route?.matched?.some((record) => record.name === 'SpacesDetailTasks') ?? false
)

/** 题目列表那几格（全部 / 各分类）亮不亮：只看地址里的分类。 */
function isTasksLinkActive(query: Record<string, string> = {}): boolean {
  if (!isUnderTasksSection.value) return false
  const category = nav?.route?.query?.category
  const current = typeof category === 'string' ? category : undefined
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
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
