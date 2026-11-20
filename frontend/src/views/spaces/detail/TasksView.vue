<script setup lang="ts">
// 题目列表页「画 + 交互」的那一半。只认 props、发 emit：范围/搜索/排序/分类、分页与
// 「我发布的」的加载状态都由容器（Tasks.vue）持有，这里只负责摆出来。
//
// 两处经容器算好再传进来的东西：
// - 顶上那栏置顶公告：容器取「当前」公告、算好公告页地址（PinnedAnnouncements 只认 props）。
// - 每一行点去哪：题目页地址（带列表的筛选）由容器的 rowTo 算（TaskRow 只认 props）。
// 因此这里不读路由、不连 API，在没装路由、没连 API 的树里也能渲染。
import type { UserRefTarget } from '@/lib/userRef'
import type { SpaceAnnouncement, SpaceMyPublishedTask, Task, Topic } from '@/types'
import type { TaskScope, TaskSortKey } from './taskListFilters'

import { useI18n } from 'vue-i18n'

import PinnedAnnouncements from './PinnedAnnouncements.vue'
import PublishedTaskRow from './PublishedTaskRow.vue'
import TaskListToolbar from './TaskListToolbar.vue'
import TaskRow from './TaskRow.vue'

import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import InfiniteScroll from '@/components/common/InfiniteScroll.vue'

defineProps<{
  scope: TaskScope
  pendingOnly: boolean
  hotTopics: Topic[]
  categoryFilterOptions: { title: string; value: number | null }[]
  spaceId: number
  /** 顶上那栏：这个空间「当前」的公告（哪些已到期由服务端分好）。 */
  announcements: SpaceAnnouncement[]
  /** 公告页地址。 */
  announcementsTarget: UserRefTarget
  /** 每一行的去处：题目页地址，带列表的筛选。 */
  rowTo: (task: Task) => UserRefTarget
  loadingMore: boolean
  hasMore: boolean
  refreshing: boolean
  tasks: Task[]
  total: number
  /** 「全部」最上面「我参与的」那一段：我手上有一份的题，不收起。别的范围是空的。 */
  mineTasks: Task[]
  mineTotal: number
  mineHasMore: boolean
  mineLoadingMore: boolean
  /** 「全部」里已截止或名额已满的那些：收在列表末尾，点开才画。别的范围是空的。 */
  closedTasks: Task[]
  closedTotal: number
  closedHasMore: boolean
  closedLoadingMore: boolean
  publishedLoading: boolean
  publishedFailed: boolean
  publishedError: string | null
  publishedTasks: SpaceMyPublishedTask[]
}>()

const emit = defineEmits<{
  'update:scope': [scope: TaskScope]
  'update:pendingOnly': [on: boolean]
  loadMore: []
  loadMoreClosed: []
  loadMoreMine: []
  loadPublishedTasks: []
}>()

const selectedTopics = defineModel<number[]>('selectedTopics', { required: true })
const sortKey = defineModel<TaskSortKey>('sort', { required: true })
const searchQuery = defineModel<string>('search', { required: true })
const selectedCategoryIdModel = defineModel<number | null>('selectedCategory', { required: true })
const closedOpen = defineModel<boolean>('closedOpen', { default: false })

const { t } = useI18n()
</script>

<template>
  <v-sheet flat rounded="lg" class="task-container">
    <!-- 顶部导航和筛选区 -->
    <div class="filter-section pa-4 pb-0">
      <PinnedAnnouncements class="mb-4" :current="announcements" :target="announcementsTarget" />
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
        :search="searchQuery"
        @update:scope="emit('update:scope', $event)"
        @update:pending-only="emit('update:pendingOnly', $event)"
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
        @retry="emit('loadPublishedTasks')"
      />
      <BaseEmptyState
        v-else-if="!publishedTasks.length"
        icon="mdi-pencil-box-multiple-outline"
        :title="t('spaces.detail.tasks.noTasks')"
      />
      <template v-else>
        <PublishedTaskRow v-for="task in publishedTasks" :key="task.taskId" :task="task" :space-id="spaceId" />
      </template>
    </div>
    <div v-else class="tasks-list">
      <!-- The tasks the viewer holds a claim on: their own group on top, never folded. -->
      <section v-if="mineTotal" class="tasks-mine" data-testid="tasks-mine">
        <h2 class="tasks-group">{{ t('spaces.detail.tasks.mineGroup', { n: mineTotal }) }}</h2>
        <InfiniteScroll
          :loading="mineLoadingMore"
          :has-more="mineHasMore"
          :initial-loading="false"
          :is-empty="mineTasks.length === 0"
          :shown="mineTasks.length"
          :total="mineTotal"
          @load-more="emit('loadMoreMine')"
        >
          <template #no-more><span /></template>
          <TaskRow v-for="task in mineTasks" :key="task.id" :task="task" :to="rowTo(task)" />
        </InfiniteScroll>
      </section>
      <InfiniteScroll
        :loading="loadingMore"
        :has-more="hasMore"
        :initial-loading="refreshing"
        :is-empty="tasks.length === 0"
        :shown="tasks.length"
        :total="total"
        @load-more="emit('loadMore')"
      >
        <template #empty>
          <!-- Below the viewer's own group, an empty rest says nothing. -->
          <span v-if="mineTotal" />
          <BaseEmptyState
            v-else
            icon="mdi-trophy"
            :title="closedTotal ? t('spaces.detail.tasks.noOpenTasks') : t('spaces.detail.tasks.noTasks')"
          />
        </template>
        <!-- The folded row below ends this list; an end marker above it would say nothing follows. -->
        <template v-if="closedTotal" #no-more><span /></template>
        <TaskRow v-for="task in tasks" :key="task.id" :task="task" :to="rowTo(task)" />
      </InfiniteScroll>

      <!-- Closed or full: below everything that can still be claimed, folded until asked for. -->
      <section v-if="closedTotal && !hasMore && !refreshing" class="tasks-closed" data-testid="tasks-closed">
        <button
          type="button"
          class="tasks-closed__toggle"
          :aria-expanded="closedOpen"
          @click="closedOpen = !closedOpen"
        >
          <v-icon size="18" class="tasks-closed__chevron" :class="{ 'tasks-closed__chevron--open': closedOpen }"
            >mdi-chevron-right</v-icon
          >
          <span>{{ t('spaces.detail.tasks.closed', { n: closedTotal }) }}</span>
        </button>
        <InfiniteScroll
          v-if="closedOpen"
          :loading="closedLoadingMore"
          :has-more="closedHasMore"
          :initial-loading="false"
          :is-empty="closedTasks.length === 0"
          :shown="closedTasks.length"
          :total="closedTotal"
          @load-more="emit('loadMoreClosed')"
        >
          <TaskRow v-for="task in closedTasks" :key="task.id" :task="task" :to="rowTo(task)" />
        </InfiniteScroll>
      </section>
    </div>
  </v-sheet>
</template>

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

.tasks-group {
  margin: 0;
  padding: 14px 8px;
  border-bottom: 1px solid var(--line);
  color: var(--muted);
  font-size: 14px;
  font-weight: 400;
  line-height: var(--lh-14);
}

.tasks-closed__toggle {
  display: flex;
  gap: 4px;
  align-items: center;
  width: 100%;
  padding: 14px 8px;
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--muted);
  text-align: left;
  border-bottom: 1px solid var(--line);
}

.tasks-closed__toggle:hover {
  color: var(--ink);
  background: var(--fill);
}

.tasks-closed__chevron {
  transition: transform var(--dur-quick) var(--ease-standard);
}

.tasks-closed__chevron--open {
  transform: rotate(90deg);
}
</style>
