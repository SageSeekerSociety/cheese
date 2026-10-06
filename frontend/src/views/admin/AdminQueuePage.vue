<script setup lang="ts">
import { ref } from 'vue'

import { useAdminQueue } from '@/composables/useAdminQueue'

import AdminQueuePageView from '@/views/admin/AdminQueuePageView.vue'

// 管理后台的反馈队列（`/admin/queue`，§4.1）。**这一页是这一轮的主屏** —— 管理侧的
// 全部日常动作都在这上面发生，详情、看板、成员都是它的支线。
//
// 这一件现在只是**容器**：判断与动作在 `composables/useAdminQueue.ts`，画面全部在
// 同目录的 `AdminQueuePageView.vue`（只吃 props、只往上发事件，能单独挂进预览站看）。
// 三件以前长在这一页里、现在各归各位的东西：
//
//   1. **问法**（栏位 / 关键词 / 三个日期窗口）在 store 里，这一页只负责把它们画出来、
//      把人的动作报回去。队列本体（`AdminQueueList` / `AdminFeedbackTable`）只认
//      `items`，拿不到写入口 —— 分诊键要落 store，所以键在这一层分发（§8 的「列表」档）。
//   2. **「手上这一条是谁」**。光标、详情、已读、撤销条都挂在同一个 id 上，所以这个
//      判断只能有一份，也就是这一层的。
//   3. **四态**。加载/空/错误/筛空里，「错误」和「筛空」是页面才知道的两件事：卡片
//      只知道「我手上一条都没有」。所以两个列表组件都留了 `empty` 插槽，内容在这里 ——
//      填的那一件（`AdminQueueEmpty`）只认这一层算出来的 `state` / `copy`。
//
// **队列与总表共用一份 `adminItems`**（§13 C-10）：切视图不重新取数。代价是两个视图
// 的排序/筛选状态互相覆盖，不做的原因写在规格里 —— 多一份列表就多一次
// `/admin/feedback` 请求。
//
// `focusSearch` / `focusAssignee` 两条是**反向**的：搜索框与详情那一栏的落点都在视图
// 里，而「什么时候请它们聚焦」由这一层决定，所以视图把它们暴露出来、这里转给组合式函数。
defineOptions({ name: 'AdminQueuePage' })

const view = ref<InstanceType<typeof AdminQueuePageView> | null>(null)

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
  detailStatusItems,
  storeError,
  assigneeItems,
  assigneeLoading,
  assigneeSearch,
  assigneeHint,
  detailAssign,
  detailPriority,
  detailSecurity,
  detailNote,
  onDetailTriage,
  unread,
  markCurrentRead,
  reload,
  view: viewMode,
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
  focusSearch: () => view.value?.focusSearch(),
  focusAssignee: () => view.value?.focusAssignee() ?? false,
})

/** 「指派给谁」那个下拉的输入串。它是组合式函数里的一个 ref，视图只往上报新值。 */
function setAssigneeSearch(v: string) {
  assigneeSearch.value = v
}
</script>

<template>
  <AdminQueuePageView
    ref="view"
    :is-wide="isWide"
    :detail-open="detailOpen"
    :triage-open="triageOpen"
    :detail="detail"
    :detail-loading="detailLoading"
    :detail-error="detailError"
    :detail-status-items="detailStatusItems"
    :store-error="storeError"
    :assignee-items="assigneeItems"
    :assignee-loading="assigneeLoading"
    :assignee-search="assigneeSearch"
    :assignee-hint="assigneeHint"
    :unread="unread"
    :view="viewMode"
    :admin-tab="adminTab"
    :lane-options="laneOptions"
    :draft="draft"
    :window-chips="windowChips"
    :status-tab="statusTab"
    :tab-options="tabOptions"
    :visible="visible"
    :cursor-index="cursorIndex"
    :cursor-id="cursorId"
    :show-skeleton="showSkeleton"
    :show-status-word="showStatusWord"
    :state="state"
    :copy="copy"
    :scoped="scoped"
    :has-prev="hasPrev"
    :has-next="hasNext"
    :undo="undo"
    @close="closeDetail"
    @update:detail-open="setDetailOpen"
    @update:triage-open="setTriageOpen"
    @triage="onDetailTriage"
    @assign="detailAssign"
    @priority="detailPriority"
    @security="detailSecurity"
    @note="detailNote"
    @update:assignee-search="setAssigneeSearch"
    @mark-read="markCurrentRead"
    @reload="reload"
    @update:view="setView"
    @select-lane="selectLane"
    @update:draft="setDraft"
    @update:status="setStatusTab"
    @clear-window="clearWindow"
    @update:active-index="onActiveIndex"
    @activate="onTableActivate"
    @open="openItem"
    @advance="advance"
    @action="runAction"
    @prev="prev"
    @next="next"
    @undo="undoTriage"
    @dismiss="dismissUndo"
  />
</template>
