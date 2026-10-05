<script setup lang="ts">
// 题目列表页「落网」的那一半：读题目列表（分页）、读「我发布的」、读热门话题与分类，
// 以及范围/搜索/排序/分类这些地址参数怎么写回地址栏。画面那一半在 TasksView.vue。
// 顶上那栏置顶公告的「当前」公告、每一行点去哪，都在这里算好当 prop 传进去。
//
// 「发布题目」是这一页的主操作：桌面上在页头右边，手机上是顶栏右边那一颗。
import type { UserRefTarget } from '@/lib/userRef'
import type { SpaceMyPublishedTask } from '@/network/api/spaces/types'
import type { Task, Topic } from '@/types'
import type { TaskScope, TaskSortKey } from './taskListFilters'

import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'

import { createEmptyResult, usePaging } from '@/utils/paging'

import { useSpaceData } from '@/composables/useSpaceData'

import TasksView from './TasksView.vue'
import { useSpaceAnnouncements } from './useSpaceAnnouncements'

import { useCommands } from '@/commands'
import { SpacesApi } from '@/network/api/spaces'
import { TasksApi } from '@/network/api/tasks'
import { useSpaceStore } from '@/stores/space'

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
const searchQuery = ref('')
// 选了几个话题就是「带其中任一个」。
const selectedTopics = ref<number[]>([])

const { t } = useI18n()

const spaceStore = useSpaceStore()
const spaceData = useSpaceData()
const { currentSpace, categories } = storeToRefs(spaceStore)

const spaceId = computed(() => Number(route.params.spaceId))

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
  total,
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

/** 顶上那栏置顶公告：读「当前」公告，并把公告页地址备好给画面那一半。 */
const { current: announcements } = useSpaceAnnouncements(() => Number(route.params.spaceId))
const announcementsTarget = computed(() => ({
  name: 'SpacesAnnouncements',
  params: { spaceId: Number(route.params.spaceId) },
}))

/** 每一行的去处：题目页地址，带着列表的筛选 —— 从题目页返回时列表还是原来那样。 */
const rowTo = (task: Task): UserRefTarget => ({
  name: 'TasksDetail',
  params: { taskId: task.id },
  query: route.query,
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

<template>
  <TasksView
    v-model:search="searchQuery"
    v-model:selected-category="selectedCategoryIdModel"
    v-model:selected-topics="selectedTopics"
    v-model:sort="sortKey"
    :announcements="announcements"
    :announcements-target="announcementsTarget"
    :category-filter-options="categoryFilterOptions"
    :has-more="hasMore"
    :hot-topics="hotTopics"
    :loading-more="loadingMore"
    :pending-only="pendingOnly"
    :published-error="publishedError"
    :published-failed="publishedFailed"
    :published-loading="publishedLoading"
    :published-tasks="visiblePublishedTasks"
    :refreshing="refreshing"
    :row-to="rowTo"
    :scope="scope"
    :space-id="spaceId"
    :tasks="tasks"
    :total="total"
    @load-more="loadMore"
    @load-published-tasks="loadPublishedTasks"
    @update:pending-only="setPendingOnly"
    @update:scope="selectScope"
  />
</template>
