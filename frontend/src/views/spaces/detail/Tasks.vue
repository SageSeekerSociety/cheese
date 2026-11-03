<script setup lang="ts">
// 题目列表页「落网」的那一半：读题目列表（分页）、读「我发布的」、读热门话题与分类，
// 以及范围/搜索/排序/分类这些地址参数怎么写回地址栏。画面那一半在 TasksView.vue。
// 顶上那栏置顶公告的「当前」公告、每一行点去哪，都在这里算好当 prop 传进去。
//
// 「发布题目」是这一页的主操作：桌面上在页头右边，手机上是顶栏右边那一颗。
import type { UserRefTarget } from '@/lib/userRef'
import type { SpaceMyPublishedTask, Task, Topic } from '@/types'
import type { TaskScope, TaskSortKey } from './taskListFilters'

import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'

import { createEmptyResult, usePaging } from '@/utils/paging'

import { useSpaceData } from '@/composables/useSpaceData'

import TasksView from './TasksView.vue'
import { useSpaceAnnouncements } from './useSpaceAnnouncements'

import { SpacesApi } from '@/api/spaces'
import { TaskBoardApi } from '@/api/taskBoard'
import { useCommands } from '@/commands'
import { markTasksSeen } from '@/lib/spaceTasksSeen'
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
  /** 「全部」先列还能领的，其余的另读一份、收在列表末尾。 */
  lifecycle?: 'recruiting' | 'closed'
}

// 「全部」是一张表，分三段（Zhifei 2026-10-26）：最上面「我参与的」——我手上有一份的题，不管题目
// 开着还是截止，不收起；下面是别的题里还能领的；最后是别的题里已截止或名额已满的，收起。下面两段
// 按题目自己的状态分，对谁都一样（`joined: false` 把我手上的题留在上面那段）。「我参与的」那一格
// 列的就是最上面那一段。

const route = useRoute()
const router = useRouter()
const searchQuery = ref('')
// 选了几个话题就是「带其中任一个」。
const selectedTopics = ref<number[]>([])

const { t } = useI18n()

const spaceStore = useSpaceStore()
const spaceData = useSpaceData()
const { categories } = storeToRefs(spaceStore)

const spaceId = computed(() => Number(route.params.spaceId))

const hotTopics = ref<Topic[]>([])

const fetchHotTopics = async () => {
  const sId = Number(route.params.spaceId)
  if (!sId) return
  try {
    const data = await SpacesApi.getSpaceTopics(sId, 20, 'popularity')
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
  joined: scope.value === 'participating' ? true : scope.value === 'all' ? false : undefined,
  lifecycle: scope.value === 'all' ? 'recruiting' : undefined,
}))

/** 「已截止或名额已满」那一份：同样的筛选，只是换成另一半。 */
const closedOptions = computed<QueryOptions>(() => ({ ...queryOptions.value, lifecycle: 'closed' }))
/** 「全部」最上面「我参与的」那一段：同样的筛选，只要我手上有的，不分开着还是截止。 */
const mineOptions = computed<QueryOptions>(() => ({ ...queryOptions.value, joined: true, lifecycle: undefined }))

async function listTasks(pageStart: string | undefined, options: QueryOptions | undefined) {
  if (!options || !options.space) return createEmptyResult<Task, string>()
  const data = await TaskBoardApi.list({
    space: options.space,
    pageStart: pageStart,
    sort_by: options.by ?? 'createdAt',
    sort_order: options.order ?? 'desc',
    keywords: options.keywords,
    approved: 'APPROVED',
    topics: options.topics,
    categoryId: options.categoryId,
    joined: options.joined,
    lifecycle: options.lifecycle,
    queryTopics: true,
    queryJoined: true,
  })
  return { data: data.tasks, page: data.page }
}

const {
  data: tasks,
  loadMore,
  reset,
  hasMore,
  refreshing,
  loadingMore,
  total,
} = usePaging<Task, QueryOptions, string>(listTasks, undefined, queryOptions.value)

const closed = usePaging<Task, QueryOptions, string>(listTasks, undefined, closedOptions.value)
const mine = usePaging<Task, QueryOptions, string>(listTasks, undefined, mineOptions.value)
/** 收起来的那一段开着没有；换了筛选就收回去。 */
const closedOpen = ref(false)

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
    const data = await SpacesApi.getMyPublishedTasks(spaceId, {
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
const announcementsTarget = computed<UserRefTarget>(() => ({
  name: 'SpacesAnnouncements',
  params: { spaceId: Number(route.params.spaceId) },
}))

/** 每一行的去处：题目页地址，带着列表的筛选 —— 从题目页返回时列表还是原来那样。 */
const rowTo = (task: Task): UserRefTarget => ({
  name: 'TasksDetail',
  params: { spaceId: spaceId.value, taskId: task.id },
  query: route.query,
})

// 从某个分类的列表过来时，把它带过去预选。
const navigateToPublishTask = () => {
  const query: Record<string, string> = {}
  if (selectedCategoryId.value) query.categoryId = String(selectedCategoryId.value)
  router.push({ name: 'SpacesDetailPublishTask', params: { spaceId: route.params.spaceId }, query })
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
    closedOpen.value = false
    // 先读一页，收起时那一行就能写出有几道。
    if (value === 'all') {
      closed.reset(undefined, closedOptions.value)
      void closed.loadMore()
      mine.reset(undefined, mineOptions.value)
      void mine.loadMore()
    }
  },
  { deep: true, immediate: true }
)

// 打开了题目列表：侧栏「全部题目」旁边的新题目数从这一刻重新算。
watch(
  spaceId,
  (id) => {
    if (!id) return
    markTasksSeen(id)
    spaceStore.setNewTaskCount(0)
  },
  { immediate: true }
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
    v-model:closed-open="closedOpen"
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
    :mine-tasks="scope === 'all' ? mine.data.value : []"
    :mine-total="scope === 'all' ? mine.total.value : 0"
    :mine-has-more="mine.hasMore.value"
    :mine-loading-more="mine.loadingMore.value"
    :closed-tasks="scope === 'all' ? closed.data.value : []"
    :closed-total="scope === 'all' ? closed.total.value : 0"
    :closed-has-more="closed.hasMore.value"
    :closed-loading-more="closed.loadingMore.value"
    @load-more="loadMore"
    @load-more-closed="closed.loadMore"
    @load-more-mine="mine.loadMore"
    @load-published-tasks="loadPublishedTasks"
    @update:pending-only="setPendingOnly"
    @update:scope="selectScope"
  />
</template>
