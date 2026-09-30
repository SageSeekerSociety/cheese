<script setup lang="ts">
import { ref } from 'vue'

import { useAdminQueue } from '@/composables/useAdminQueue'

import AdminFeedbackTable from '@/components/admin/AdminFeedbackTable.vue'
import AdminQueueDetail from '@/components/admin/AdminQueueDetail.vue'
import AdminQueueFoot from '@/components/admin/AdminQueueFoot.vue'
import AdminQueueList from '@/components/admin/AdminQueueList.vue'
import AdminQueueEmpty from '@/components/admin/queue/AdminQueueEmpty.vue'
import AdminQueueHeader from '@/components/admin/queue/AdminQueueHeader.vue'
import AdminQueueToolbar from '@/components/admin/queue/AdminQueueToolbar.vue'
import UndoStrip from '@/components/admin/UndoStrip.vue'
import AdminFeedbackDetailDrawer from '@/components/feedback/AdminFeedbackDetailDrawer.vue'

// 管理后台的反馈队列（`/admin/queue`，§4.1）。**这一页是这一轮的主屏** —— 管理侧的
// 全部日常动作都在这上面发生，详情、看板、成员都是它的支线。
//
// 这一件现在只剩**接线**：判断与动作在 `composables/useAdminQueue.ts`，页头 / 工具行 /
// 四态块的画法在 `components/admin/queue/*.vue`，两条对应关系（哪一件点哪一下调哪个
// 动作）看下面那个模板就够了。三件以前长在这一页里、现在各归各位的东西：
//
//   1. **问法**（栏位 / 关键词 / 三个日期窗口）在 store 里，这一页只负责把它们画出来、
//      把人的动作报回去。队列本体（`AdminQueueList` / `AdminFeedbackTable`）只认
//      `items`，拿不到写入口 —— 分诊键要落 store，所以键在 composable 那一层分发
//      （§8 的「列表」档）。
//   2. **「手上这一条是谁」**。光标、详情、已读、撤销条都挂在同一个 id 上，所以这个
//      判断只能有一份，也就是 composable 那一份。`AdminQueueDetail` 的注释把同一个
//      理由写了一遍。
//   3. **四态**。加载/空/错误/筛空里，「错误」和「筛空」是页面才知道的两件事：卡片
//      只知道「我手上一条都没有」。所以两个列表组件都留了 `empty` 插槽，内容在这里 ——
//      填的那一件（`AdminQueueEmpty`）只认 composable 算出来的 `state` / `copy`。
//
// **队列与总表共用一份 `adminItems`**（§13 C-10）：切视图不重新取数。代价是两个视图
// 的排序/筛选状态互相覆盖，不做的原因写在规格里 —— 多一份列表就多一次
// `/admin/feedback` 请求。
defineOptions({ name: 'AdminQueuePage' })

/** 搜索框住在 `AdminQueueToolbar` 里，而 `/` 键和清窗口之后都要把焦点送进去。
 *  位置是那一件的事、时机是这一层的事，所以这里只交给 composable 一个「怎么办到」。 */
const toolbarRef = ref<InstanceType<typeof AdminQueueToolbar> | null>(null)

const {
  detailRef,
  isWide,
  detailOpen,
  setDetailOpen,
  closeDetail,
  triageOpen,
  setTriageOpen,
  detail,
  detailLoading,
  detailError,
  onDetailTriage,
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
  listError,
  runAction,
  scoped,
  hasPrev,
  hasNext,
  prev,
  next,
  undo,
  undoTriage,
  dismissUndo,
} = useAdminQueue({ focusSearch: () => toolbarRef.value?.focusSearch() })
</script>

<template>
  <div class="qpage">
    <!-- 宽屏的详情是**整页接管**（§4.4：200 导航 + 440 左栏 + 760 右栏）。队列行本身
         就要 1100px，两个并排在任何常见视口里都塞不下 —— 塞得下的那种宽度下，
         `AdminQueueDetail` 自己那条 1280 媒体查询又已经把它拆成两栏了。 -->
    <AdminQueueDetail
      v-if="isWide && detailOpen"
      ref="detailRef"
      :item="detail"
      :loading="detailLoading"
      :error="detailError"
      :triage-open="triageOpen"
      @close="closeDetail"
      @update:triage-open="setTriageOpen"
      @triage="onDetailTriage"
    />

    <template v-else>
      <div class="qpage__inner">
        <AdminQueueHeader
          :unread="unread"
          :view="view"
          @mark-read="markCurrentRead"
          @refresh="reload"
          @update:view="setView"
        />

        <AdminQueueToolbar
          ref="toolbarRef"
          :lane="adminTab"
          :lanes="laneOptions"
          :query="draft"
          :chips="windowChips"
          :status="statusTab"
          :status-options="tabOptions"
          :show-scope-note="statusTab !== 'all'"
          @update:query="setDraft"
          @update:status="setStatusTab"
          @select-lane="selectLane"
          @clear-window="clearWindow"
        />

        <!-- 队列与总表**同时只挂一个**（§8 末：视图切换时解绑），否则同一个 `j` 会被两个
             `role="grid"` 各收一次。 -->
        <AdminQueueList
          v-if="view === 'list'"
          :items="visible"
          :active-index="cursorIndex"
          :loading="showSkeleton"
          :show-status-word="showStatusWord"
          @update:active-index="onActiveIndex"
          @advance="advance"
          @open="openItem"
        >
          <template #empty>
            <AdminQueueEmpty
              :title="copy.title"
              :desc="copy.desc"
              :action="copy.action"
              :tone="state === 'error' ? 'error' : 'neutral'"
              :raw="state === 'error' ? listError : null"
              @action="runAction"
            />
          </template>

          <template #foot>
            <!-- 脚的内容两个视图共用一份（`AdminQueueFoot`），壳在各自的列表组件里。 -->
            <AdminQueueFoot
              :scope="scoped"
              :rows="visible.length"
              :has-prev="hasPrev"
              :has-next="hasNext"
              @prev="prev"
              @next="next"
            />
          </template>
        </AdminQueueList>

        <AdminFeedbackTable
          v-else
          :items="visible"
          :active-id="cursorId"
          :loading="showSkeleton"
          @activate="onTableActivate"
          @open="openItem"
        >
          <template #empty>
            <AdminQueueEmpty
              :title="copy.title"
              :desc="copy.desc"
              :action="copy.action"
              :tone="state === 'error' ? 'error' : 'neutral'"
              :raw="state === 'error' ? listError : null"
              @action="runAction"
            />
          </template>

          <template #foot>
            <AdminQueueFoot
              :scope="scoped"
              :rows="visible.length"
              :has-prev="hasPrev"
              :has-next="hasNext"
              @prev="prev"
              @next="next"
            />
          </template>
        </AdminFeedbackTable>
      </div>
    </template>

    <!-- <1280：详情是那个 520px 的抽屉（§4.4「右栏变 520px 抽屉」）。它在两种宽度下
         都是同一个组件：`AdminQueueDetail` 自己那条 1280 媒体查询按**视口**分档，所以
         抽屉只在视口也小于 1280 时才会走到单栏形态 —— 这也正是它只在窄屏出现的理由。 -->
    <AdminFeedbackDetailDrawer
      v-if="!isWide"
      :open="detailOpen"
      :item="detail"
      :loading="detailLoading"
      :error="detailError"
      :triage-open="triageOpen"
      @update:open="setDetailOpen"
      @update:triage-open="setTriageOpen"
      @triage="onDetailTriage"
    />

    <UndoStrip v-if="undo" :message="undo.message" @undo="undoTriage" @dismiss="dismissUndo" />
  </div>
</template>

<style scoped>
.qpage {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: var(--canvas);
}

/* 内容列锁 1440（--page-w-admin）：16 + 4 + 12 + F + 16 + 116 + 16 + 88 + 16 +
   B + 20 = 1440，即 F = 1156 − B（B 是 max-content 的推进按钮，56–84 → F ≈ 1072–1100）。
   F 的下限仍是 740：可用区不足时整行在 `.qlist` 里横着滚，窄屏行为和 1100 时代一致。
   居中而不是靠左：这一页的右边没有东西，靠左会让不同视口下的行宽差出一截。 */
.qpage__inner {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  width: 100%;
  max-width: var(--page-w-admin);
  min-height: 0;
  margin: 0 auto;
}

/* 无效按键的闪底。0.2s（§7.7 的「出现 / 消失」那一档），中性色 —— 一次落空的按键
   不该借状态三连色里的任何一支说话。类名是这一层加上去的（`flashRow` 直接改 DOM），
   而行长在两个列表组件里，所以这一条必须是 `:deep`。 */
.qpage :deep(.qflash) {
  background: var(--fill-2);
  transition: background-color 0.2s ease;
}
</style>
