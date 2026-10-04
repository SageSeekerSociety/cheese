<template>
  <v-sheet flat rounded="lg" class="task-container">
    <!-- 顶部导航和筛选区 -->
    <div class="filter-section pa-4 pb-0">
      <PinnedAnnouncements class="mb-4" />
      <!-- 主分类选项按钮在移动端显示 -->
      <div class="d-md-none category-nav-mobile mb-4">
        <v-select
          v-model="selectedCategoryIdModel"
          autocomplete="off"
          :items="categoryFilterOptions"
          density="comfortable"
          variant="outlined"
          hide-details
          class="category-select"
        ></v-select>
      </div>

      <TaskListToolbar
        class="mb-4"
        :scope="scope"
        :pending-only="pendingOnly"
        :topics="hotTopics"
        :selected-topics="selectedTopics"
        :sort="sortKey"
        :search="searchQuery ?? ''"
        @update:scope="selectScope"
        @update:pending-only="setPendingOnly"
        @update:selected-topics="selectedTopics = $event"
        @update:sort="sortKey = $event"
        @search="searchQuery = $event"
      />
    </div>

    <v-divider class="mt-0"></v-divider>

    <!-- 「我发布的」包括还没过审和被驳回的题：通用列表对出题人自己也只给已通过的，
         所以这一格走「我发布的题目」接口，卡片上带审核状态。 -->
    <div v-if="scope === 'publishing'" class="tasks-list">
      <template v-if="publishedLoading && !publishedTasks.length">
        <v-skeleton-loader v-for="index in 3" :key="index" type="list-item-three-line" />
      </template>
      <BaseLoadError
        v-else-if="publishedFailed"
        :title="t('spaces.detail.tasks.loadFailed')"
        :error="publishedError"
        @retry="loadPublishedTasks"
      />
      <BaseEmptyState
        v-else-if="!visiblePublishedTasks.length"
        icon="mdi-pencil-box-multiple-outline"
        :title="t('spaces.detail.tasks.noTasks')"
      />
      <template v-else>
        <PublishedTaskRow
          v-for="task in visiblePublishedTasks"
          :key="task.taskId"
          :task="task"
          :space-id="Number(route.params.spaceId)"
        />
      </template>
    </div>
    <div v-else class="tasks-list">
      <infinite-scroll
        :loading="loadingMore"
        :has-more="hasMore"
        :initial-loading="refreshing"
        :is-empty="tasks.length === 0"
        @load-more="loadMore"
      >
        <template #empty>
          <BaseEmptyState icon="mdi-trophy" :title="t('spaces.detail.tasks.noTasks')" />
        </template>
        <TaskRow v-for="task in tasks" :key="task.id" :task="task" :query="route.query" />
      </infinite-scroll>
    </div>
  </v-sheet>
</template>

<script setup lang="ts">
import type { SpaceMyPublishedTask } from '@/network/api/spaces/types'
import type { Task, Topic } from '@/types'
import type { TaskScope, TaskSortKey } from './taskListFilters'

import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'

import { createEmptyResult, usePaging } from '@/utils/paging'

import { useSpaceData } from '@/composables/useSpaceData'

import PublishedTaskRow from './PublishedTaskRow.vue'
import TaskListToolbar from './TaskListToolbar.vue'
import TaskRow from './TaskRow.vue'

import { useCommands } from '@/commands'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import InfiniteScroll from '@/components/common/InfiniteScroll.vue'
import { SpacesApi } from '@/network/api/spaces'
import { TasksApi } from '@/network/api/tasks'
import { useSpaceStore } from '@/stores/space'
import PinnedAnnouncements from '@/views/spaces/detail/PinnedAnnouncements.vue'

type SortBy = 'createdAt' | 'updatedAt' | 'deadline'
type SortOrder = 'asc' | 'desc'

const SORTS: Record<TaskSortKey, { by: SortBy; order: SortOrder }> = {
  latestPublished: { by: 'createdAt', order: 'desc' },
  latestUpdated: { by: 'updatedAt', order: 'desc' },
  nearestDeadline: { by: 'deadline', order: 'asc' },
}

type QueryOptions = {
  space: number
  by: SortBy
  order: SortOrder
  keywords?: string
  topics?: number[]
  categoryId?: number
  joined?: boolean
}

const route = useRoute()
const router = useRouter()
const searchQuery = ref<string>()
// 选了几个话题就是「带其中任一个」。
const selectedTopics = ref<number[]>([])

const { t } = useI18n()

const spaceStore = useSpaceStore()
const spaceData = useSpaceData()
const { currentSpace, categories } = storeToRefs(spaceStore)

const hotTopics = ref<Topic[]>([])

const fetchHotTopics = async () => {
  const sId = Number(route.params.spaceId)
  if (!sId) return
  try {
    const { data } = await SpacesApi.getSpaceTopics(sId, 20, 'popularity')
    hotTopics.value = data.topics || []
  } catch (e) {
    console.error('Fetch top topics failed', e)
  }
}

// 获取活跃分类列表
const activeCategories = computed(() => {
  return categories.value.filter((category) => !category.archivedAt).sort((a, b) => a.displayOrder - b.displayOrder)
})

// 获取当前选中的分类ID（从URL参数）
const selectedCategoryId = computed<number | null>(() => {
  const categoryParam = route.query.category
  if (!categoryParam) return null

  const categoryId = Number(categoryParam)
  // 确保分类在活跃分类列表中
  return activeCategories.value.some((cat) => cat.id === categoryId) ? categoryId : null
})

const scope = computed<TaskScope>(() => {
  const value = route.query.filter
  return value === 'participating' || value === 'publishing' ? value : 'all'
})

const selectScope = (value: TaskScope) => {
  const query = { ...route.query }
  if (value === 'all') delete query.filter
  else query.filter = value
  if (value !== 'publishing') delete query.pending
  router.replace({ name: 'SpacesDetailTasksList', params: { spaceId: route.params.spaceId }, query })
}

/** 「只看待处理」：只在「我发布的」下生效，写在地址的 `pending=1` 里。 */
const pendingOnly = computed(() => scope.value === 'publishing' && route.query.pending === '1')

const setPendingOnly = (on: boolean) => {
  const query = { ...route.query }
  if (on) query.pending = '1'
  else delete query.pending
  router.replace({ name: 'SpacesDetailTasksList', params: { spaceId: route.params.spaceId }, query })
}

const categoryFilterOptions = computed(() => [
  { title: t('spaces.detail.allContests'), value: null },
  ...activeCategories.value.map((category) => ({
    title: category.name,
    value: category.id,
  })),
])

const selectedCategoryIdModel = computed({
  get() {
    return selectedCategoryId.value
  },
  set(value: null | number) {
    const query = { ...route.query }
    if (value) query.category = String(value)
    else delete query.category
    router.replace({ name: 'SpacesDetailTasksList', params: { spaceId: route.params.spaceId }, query })
  },
})

const sortKey = ref<TaskSortKey>('latestPublished')

const queryOptions = computed<QueryOptions>(() => ({
  space: Number(route.params.spaceId),
  ...SORTS[sortKey.value],
  keywords: searchQuery.value ? searchQuery.value : undefined,
  topics: selectedTopics.value.length ? [...selectedTopics.value] : undefined,
  categoryId: selectedCategoryId.value || undefined,
  joined: scope.value === 'participating' ? true : undefined,
}))

const {
  data: tasks,
  loadMore,
  reset,
  hasMore,
  refreshing,
  loadingMore,
} = usePaging<Task, QueryOptions, string>(
  async (pageStart, queryOptions) => {
    if (!queryOptions || !queryOptions.space) return createEmptyResult<Task, string>()
    const { data } = await TasksApi.list({
      space: queryOptions.space,
      pageStart: pageStart,
      sort_by: queryOptions?.by ?? 'createdAt',
      sort_order: queryOptions?.order ?? 'desc',
      keywords: queryOptions?.keywords,
      approved: 'APPROVED',
      topics: queryOptions.topics,
      categoryId: queryOptions.categoryId,
      joined: queryOptions.joined,
      queryTopics: true,
      queryJoined: true,
    })
    return { data: data.tasks, page: data.page }
  },
  undefined,
  queryOptions.value
)

const publishedTasks = ref<SpaceMyPublishedTask[]>([])
const publishedLoading = ref(false)
// 读失败和「还没发过题」是两件事：失败替换掉这一格，空状态才说「暂无」。
const publishedFailed = ref(false)
const publishedError = ref<string | null>(null)

const loadPublishedTasks = async () => {
  const spaceId = Number(route.params.spaceId)
  if (!spaceId) return
  publishedLoading.value = true
  publishedFailed.value = false
  publishedError.value = null
  try {
    const { data } = await SpacesApi.getMyPublishedTasks(spaceId, {
      categoryId: selectedCategoryId.value ?? undefined,
      sortBy: 'publishedAt',
      sortOrder: 'desc',
    })
    publishedTasks.value = data.tasks
  } catch (error) {
    console.error('load my published tasks failed', error)
    publishedFailed.value = true
    publishedError.value = error instanceof Error && error.message ? error.message : null
  } finally {
    publishedLoading.value = false
  }
}

/** 接口一次给全，搜索与「只看待处理」都在本地筛。待处理 = 有报名待审核或提交待评审。 */
const visiblePublishedTasks = computed(() => {
  const keywords = searchQuery.value?.trim().toLowerCase()
  return publishedTasks.value.filter((task) => {
    if (keywords && !task.taskName.toLowerCase().includes(keywords)) return false
    if (pendingOnly.value && task.pendingParticipantApprovalCount === 0 && task.pendingReviewCount === 0) return false
    return true
  })
})

const navigateToPublishTask = async () => {
  try {
    if (currentSpace.value) {
      const taskTemplates = JSON.parse(currentSpace.value.taskTemplates || '[]')
      // 如果当前有选中的分类，将它作为查询参数传递（用于预选分类）
      const query: Record<string, string> = {}
      if (selectedCategoryId.value) {
        query.categoryId = String(selectedCategoryId.value)
      }

      if (taskTemplates.length > 0) {
        router.push({
          name: 'SpacesDetailSelectTemplate',
          params: { spaceId: route.params.spaceId },
          query,
        })
      } else {
        router.push({
          name: 'SpacesDetailPublishTask',
          params: { spaceId: route.params.spaceId },
          query,
        })
      }
    }
  } catch (error) {
    console.error('获取题目板详情失败:', error)
    // 如果出错，直接跳转到发布赛题页面
    router.push({ name: 'SpacesDetailPublishTask', params: { spaceId: route.params.spaceId } })
  }
}

// 「发布题目」是这一页的主操作：桌面上在页头右边，手机上是顶栏右边那一颗。
useCommands(() => [
  {
    id: 'tasks.publish',
    title: t('spaces.detail.tasks.publishTask'),
    icon: 'mdi-plus',
    header: { primary: true, accent: true },
    run: navigateToPublishTask,
  },
])

watch(
  [queryOptions, scope],
  ([options, value]) => {
    if (value === 'publishing') loadPublishedTasks()
    else reset(undefined, options)
  },
  { deep: true, immediate: true }
)

onMounted(async () => {
  await spaceData.fetchCategories() // 获取分类列表
  fetchHotTopics()
})
</script>

<style scoped lang="scss">
.task-container {
  border: none;
  /* 一栏题目列表：1920/2560 上铺满整屏会把每行的两头拉得很远，视线横穿整行才
     找得到右边的状态。封顶居中，和上面的筛选条同一栏。 */
  max-width: 1100px;
  margin-inline: auto;
}

.category-nav-mobile {
  .category-select {
    border-radius: 8px;
  }
}

.tasks-list {
  padding: 0 16px 16px;
}
</style>
