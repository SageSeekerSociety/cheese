<script setup lang="ts">
import { ref } from 'vue'

import { useAdminQueue } from '@/composables/useAdminQueue'

import { rewardFeedback } from '@/api/creditOffers'
import { toast } from '@/lib/toast'
import { useFeedbackStore } from '@/stores/feedback'
import AdminQueuePageView from '@/views/admin/AdminQueuePageView.vue'

// 管理后台的反馈队列（`/admin/queue` 和 `/admin/feedback`，§4.1）。**这一页是这一轮的
// 主屏** —— 管理侧的全部日常动作都在这上面发生，详情、看板、成员都是它的支线。
//
// 这一件现在只剩**接线**：判断与动作在 `composables/useAdminQueue.ts`，版式与 DOM 约定
// 在 `AdminQueuePageView.vue`，页头 / 工具行 / 四态块的画法在 `components/admin/queue/*.vue`。
//
//   1. **问法**（栏位 / 关键词 / 三个日期窗口）在 store 里，这一页只负责把它们画出来、
//      把人的动作报回去。队列本体（`AdminQueueList` / `AdminFeedbackTable`）只认
//      `items`，拿不到写入口 —— 分诊键要落 store，所以键在 composable 那一层分发
//      （§8 的「列表」档）。
//   2. **「手上这一条是谁」**。光标、详情、已读、撤销条都挂在同一个 id 上，所以这个
//      判断只能有一份，也就是 composable 那一份。`AdminQueueDetail` 的注释把同一个
//      理由写了一遍。
//   3. **四态**。加载/空/错误/筛空里，「错误」和「筛空」是页面才知道的两件事：卡片
//      只知道「我手上一条都没有」。所以两个列表组件都留了 `empty` 插槽，内容在 View 里
//      —— 填的那一件（`AdminQueueEmpty`）只认 composable 算出来的 `state` / `copy`。
//
// **队列与总表共用一份 `adminItems`**（§13 C-10）：切视图不重新取数。代价是两个视图
// 的排序/筛选状态互相覆盖，不做的原因写在规格里 —— 多一份列表就多一次
// `/admin/feedback` 请求。
defineOptions({ name: 'AdminQueuePage' })

/** 两个「把注意力送进去」的落点都住在 View 里（搜索框在工具行、指派框在详情里），
 *  所以这一层只把「怎么办到」交给 composable：位置是 View 的事，时机是 composable
 *  的事。 */
const viewRef = ref<InstanceType<typeof AdminQueuePageView> | null>(null)

const {
  isWide,
  detailOpen,
  setDetailOpen,
  closeDetail,
  triageOpen,
  setTriageOpen,
  detail,
  detailLoading,
  detailError,
  detailWriteError,
  statuses,
  onDetailTriage,
  assign,
  setPriority,
  setSecurity,
  addNote,
  assigneeSearch,
  setAssigneeSearch,
  assigneeCandidates,
  assigneeSearching,
  unread,
  markCurrentRead,
  reload,
  view,
  setView,
  adminTab,
  laneOptions,
  selectLane,
  draft,
  setDraft,
  windowChips,
  clearWindow,
  statusTab,
  setStatusTab,
  tabOptions,
  visible,
  cursorId,
  cursorIndex,
  showSkeleton,
  showStatusWord,
  onActiveIndex,
  onTableActivate,
  openItem,
  advance,
  state,
  copy,
  runAction,
  scoped,
  hasPrev,
  hasNext,
  prev,
  next,
  undo,
  undoTriage,
  dismissUndo,
} = useAdminQueue({
  focusSearch: () => viewRef.value?.focusSearch(),
  focusAssignee: () => viewRef.value?.focusAssignee(),
})

async function reward(id: string) {
  try {
    await rewardFeedback(id)
    await useFeedbackStore().loadAdminDetail(id)
    reload()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : String(e))
  }
}
</script>

<template>
  <AdminQueuePageView
    ref="viewRef"
    :is-wide="isWide"
    :detail-open="detailOpen"
    :detail="detail"
    :detail-loading="detailLoading"
    :detail-error="detailError"
    :detail-write-error="detailWriteError"
    :triage-open="triageOpen"
    :statuses="statuses"
    :candidates="assigneeCandidates"
    :search="assigneeSearch"
    :searching="assigneeSearching"
    :unread="unread"
    :view="view"
    :lane="adminTab"
    :lanes="laneOptions"
    :query="draft"
    :chips="windowChips"
    :status="statusTab"
    :status-options="tabOptions"
    :show-scope-note="statusTab !== 'all'"
    :items="visible"
    :cursor-id="cursorId"
    :cursor-index="cursorIndex"
    :loading="showSkeleton"
    :show-status-word="showStatusWord"
    :empty-title="copy.title"
    :empty-desc="copy.desc"
    :empty-action="copy.action"
    :empty-tone="state === 'error' ? 'error' : 'neutral'"
    :scope="scoped"
    :rows="visible.length"
    :has-prev="hasPrev"
    :has-next="hasNext"
    :undo="undo"
    @close-detail="closeDetail"
    @update:detail-open="setDetailOpen"
    @update:triage-open="setTriageOpen"
    @triage="onDetailTriage"
    @reward="reward"
    @assign="assign"
    @priority="setPriority"
    @security="setSecurity"
    @note="addNote"
    @search="setAssigneeSearch"
    @mark-read="markCurrentRead"
    @refresh="reload"
    @update:view="setView"
    @update:query="setDraft"
    @update:status="setStatusTab"
    @select-lane="selectLane"
    @clear-window="clearWindow"
    @update:active-index="onActiveIndex"
    @advance="advance"
    @activate="onTableActivate"
    @open="openItem"
    @action="runAction"
    @prev="prev"
    @next="next"
    @undo="undoTriage"
    @dismiss="dismissUndo"
  />
</template>
