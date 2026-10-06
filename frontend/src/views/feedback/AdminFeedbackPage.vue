<script setup lang="ts">
import { ref } from 'vue'

import { useAdminQueue } from '@/composables/useAdminQueue'

import AdminFeedbackPageView from '@/views/feedback/AdminFeedbackPageView.vue'

// `/admin/feedback` —— 上一代那一整页反馈管理，现在是**一层薄壳**（§10.2）。
//
// 内容全部搬去了 `views/admin/AdminQueuePage.vue`（队列、总表、四态、键盘），这一页
// 只剩一句话：这里还是那条队列。
//
// **为什么留着这条地址、不是删掉或重定向。** 这一页的地址在别人的书签里、在聊天记录
// 里、在工单模板里 —— 那些链接当时指的是「管理后台里管反馈的那一块」，今天它仍然指
// 同一件事。404 会让一条老链接变成一次求助；重定向到 `/admin/queue` 则是在地址栏里
// 不声不响地把人换个地方（他复制回聊天里的地址和点进去的那个对不上）。留一条同名的
// 薄壳最省事，也最不会骗人：地址不变、内容就是队列。
//
// 这一件是**容器**：它自己跑一次 `useAdminQueue`（和 `/admin/queue` 那棵树各跑各的，
// 但两边共用同一个 store，所以看到的是同一份数据），画面交给同目录的
// `AdminFeedbackPageView.vue`，那一件把队列视图原样渲染一遍。
defineOptions({ name: 'AdminFeedbackPage' })

const view = ref<InstanceType<typeof AdminFeedbackPageView> | null>(null)

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
  <AdminFeedbackPageView
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
