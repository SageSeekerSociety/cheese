<script lang="ts">
// 这一页的 props 单独立个名字并导出，好让 `/admin/feedback` 那层壳（它把这件原样渲染
// 一遍）声明同一份类型，而不是把三十条抄一遍 —— 抄的那份迟早和这里漂开。类型导出不
// 构成依赖：`import type` 在编译后就没了（所以壳那一件仍然是 A 级）。
//
// 这几行用到的类型，import 写在下面 `<script setup>` 那一个块里 —— 两个块在编译后是
// 同一个模块，作用域是共用的，各写一份反而会「重复标识符」。
export interface AdminQueuePageViewProps {
  isWide: boolean
  detailOpen: boolean
  triageOpen: boolean
  detail: FeedbackDetail | null
  detailLoading: boolean
  detailError: string | null
  detailStatusItems: { title: string; value: FeedbackStatus }[]
  storeError: string | null
  assigneeItems: AdminCandidate[]
  assigneeLoading: boolean
  assigneeSearch: string
  assigneeHint: string
  unread: number
  view: QueueView
  adminTab: AdminTab
  laneOptions: { value: AdminTab; label: string }[]
  draft: string
  /** 三个日期窗口的「已生效」小条，键就是清窗口时回传的那个键。 */
  windowChips: { key: QueueWindowKey; text: string; clearAria: string }[]
  statusTab: FeedbackStatus | 'all'
  tabOptions: { value: FeedbackStatus | 'all'; label: string }[]
  visible: FeedbackCard[]
  cursorIndex: number
  cursorId: string | null
  showSkeleton: boolean
  showStatusWord: boolean
  state: 'error' | 'filtered' | 'empty' | null
  copy: { title: string; desc?: string; action: string }
  scoped: boolean
  hasPrev: boolean
  hasNext: boolean
  undo: { id: string; from: FeedbackStatus; message: string } | null
}
</script>

<script setup lang="ts">
import type { QueueView, QueueWindowKey } from '@/composables/useAdminQueue'
import type { FeedbackCard, FeedbackDetail, FeedbackPriority, FeedbackStatus } from '@/cx_types'
import type { AdminTab } from '@/stores/feedback'
import type { AdminCandidate } from '@/types/admin'

import { ref } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminFeedbackTable from '@/components/admin/AdminFeedbackTable.vue'
import AdminPage from '@/components/admin/AdminPage.vue'
import AdminQueueDetailView from '@/components/admin/AdminQueueDetailView.vue'
import AdminQueueFoot from '@/components/admin/AdminQueueFoot.vue'
import AdminQueueList from '@/components/admin/AdminQueueList.vue'
import AdminQueueEmpty from '@/components/admin/queue/AdminQueueEmpty.vue'
import AdminQueueHeader from '@/components/admin/queue/AdminQueueHeader.vue'
import AdminQueueToolbar from '@/components/admin/queue/AdminQueueToolbar.vue'
import UndoStrip from '@/components/admin/UndoStrip.vue'
import AdminFeedbackDetailDrawerView from '@/components/feedback/AdminFeedbackDetailDrawerView.vue'

// 管理后台的反馈队列（`/admin/queue`，§4.1）**画的那一半** —— 这一页是这一轮的主屏，
// 管理侧的全部日常动作都在这上面发生。
//
// 判断与动作在 `composables/useAdminQueue.ts`，页头 / 工具行 / 四态块的画法在
// `components/admin/queue/*.vue`，详情和抽屉各是「容器 + 视图」两半。这一件只吃 props、
// 只往上发事件，所以能单独挂进预览站看。
//
// 三件以前长在页面里、现在各归各位的东西（容器那一半的注释把同一段理由写了一遍）：
//
//   1. **问法**（栏位 / 关键词 / 三个日期窗口）在 store 里，这一层只负责把它们画出来、
//      把人的动作报回去。队列本体（`AdminQueueList` / `AdminFeedbackTable`）只认
//      `items`，拿不到写入口 —— 分诊键要落 store，所以键留在了容器那一层（§8 的「列表」档）。
//   2. **「手上这一条是谁」**。光标、详情、已读、撤销条都挂在同一个 id 上，所以这个
//      判断只能有一份，也就是容器那一份。
//   3. **四态**。加载/空/错误/筛空里，「错误」和「筛空」是页面才知道的两件事：卡片
//      只知道「我手上一条都没有」。所以两个列表组件都留了 `empty` 插槽，内容由这一层填 ——
//      填的那一件（`AdminQueueEmpty`）只认容器算出来的 `state` / `copy`。
//
// **队列与总表共用一份 `adminItems`**（§13 C-10）：切视图不重新取数。代价是两个视图
// 的排序/筛选状态互相覆盖，不做的原因写在规格里 —— 多一份列表就多一次
// `/admin/feedback` 请求。
defineOptions({ name: 'AdminQueuePageView' })

defineProps<AdminQueuePageViewProps>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'update:detailOpen', v: boolean): void
  (e: 'update:triageOpen', v: boolean): void
  (e: 'triage', to: FeedbackStatus): void
  (e: 'assign', id: string, handle: string | null): void
  (e: 'priority', id: string, value: FeedbackPriority): void
  (e: 'security', id: string, value: boolean): void
  (e: 'note', id: string, body: string): void
  (e: 'update:assigneeSearch', v: string): void
  (e: 'mark-read'): void
  (e: 'reload'): void
  (e: 'update:view', v: QueueView): void
  (e: 'select-lane', lane: AdminTab): void
  (e: 'update:draft', v: string): void
  (e: 'update:status', v: FeedbackStatus | 'all'): void
  (e: 'clear-window', key: string): void
  (e: 'update:activeIndex', index: number): void
  (e: 'activate', id: string): void
  (e: 'open', id: string): void
  (e: 'advance', id: string): void
  (e: 'action'): void
  (e: 'prev'): void
  (e: 'next'): void
  (e: 'undo'): void
  (e: 'dismiss'): void
}>()

const { t } = useI18n()

/** 搜索框住在 `AdminQueueToolbar` 里，而 `/` 键和清窗口之后都要把焦点送进去。
 *  位置是那一件的事、时机是容器那一层的事，所以这里只把「怎么办到」暴露出去。
 *  详情那一栏的 `focusAssignee` 同理（它是 `A` 键的落点）。 */
const toolbarRef = ref<InstanceType<typeof AdminQueueToolbar> | null>(null)
const detailRef = ref<InstanceType<typeof AdminQueueDetailView> | null>(null)

function focusSearch() {
  toolbarRef.value?.focusSearch()
}

/** 宽屏那一栏的详情实例接住了就报 `true`；没接住（这一档没有它）时报 `false`，
 *  退到容器那一层的 DOM 兜底。 */
function focusAssignee(): boolean {
  if (!detailRef.value) return false
  detailRef.value.focusAssignee()
  return true
}

defineExpose({ focusSearch, focusAssignee })
</script>

<template>
  <div class="qpage">
    <!-- 宽屏的详情是**整页接管**（§4.4：440 左栏 + 760 右栏）。队列行本身
         就要 1100px，两个并排在任何常见视口里都塞不下 —— 塞得下的那种宽度下，
         `AdminQueueDetailView` 自己那条 1280 媒体查询又已经把它拆成两栏了。 -->
    <AdminQueueDetailView
      v-if="isWide && detailOpen"
      ref="detailRef"
      :item="detail"
      :loading="detailLoading"
      :error="detailError"
      :triage-open="triageOpen"
      :status-items="detailStatusItems"
      :store-error="storeError"
      :assignee-items="assigneeItems"
      :assignee-loading="assigneeLoading"
      :assignee-search="assigneeSearch"
      :assignee-hint="assigneeHint"
      @close="emit('close')"
      @update:triage-open="emit('update:triageOpen', $event)"
      @triage="emit('triage', $event)"
      @assign="(id: string, h: string | null) => emit('assign', id, h)"
      @priority="(id: string, v: FeedbackPriority) => emit('priority', id, v)"
      @security="(id: string, v: boolean) => emit('security', id, v)"
      @note="(id: string, body: string) => emit('note', id, body)"
      @update:assignee-search="emit('update:assigneeSearch', $event)"
    />

    <AdminPage v-else :title="t('navigation.admin.queue')" :sub="t('feedback.queue.sub')">
      <template #tools>
        <AdminQueueHeader
          :unread="unread"
          :view="view"
          @mark-read="emit('mark-read')"
          @refresh="emit('reload')"
          @update:view="emit('update:view', $event)"
        />
      </template>

      <AdminQueueToolbar
        ref="toolbarRef"
        :lane="adminTab"
        :lanes="laneOptions"
        :query="draft"
        :chips="windowChips"
        :status="statusTab"
        :status-options="tabOptions"
        :show-scope-note="statusTab !== 'all'"
        @update:query="emit('update:draft', $event)"
        @update:status="emit('update:status', $event)"
        @select-lane="emit('select-lane', $event)"
        @clear-window="emit('clear-window', $event)"
      />

      <!-- 队列与总表**同时只挂一个**（§8 末：视图切换时解绑），否则同一个 `j` 会被两个
             `role="grid"` 各收一次。 -->
      <AdminQueueList
        v-if="view === 'list'"
        :items="visible"
        :active-index="cursorIndex"
        :loading="showSkeleton"
        :show-status-word="showStatusWord"
        @update:active-index="emit('update:activeIndex', $event)"
        @advance="emit('advance', $event)"
        @open="emit('open', $event)"
      >
        <template #empty>
          <AdminQueueEmpty
            :title="copy.title"
            :desc="copy.desc"
            :action="copy.action"
            :tone="state === 'error' ? 'error' : 'neutral'"
            @action="emit('action')"
          />
        </template>

        <template #foot>
          <!-- 脚的内容两个视图共用一份（`AdminQueueFoot`），壳在各自的列表组件里。 -->
          <AdminQueueFoot
            :scope="scoped"
            :rows="visible.length"
            :has-prev="hasPrev"
            :has-next="hasNext"
            @prev="emit('prev')"
            @next="emit('next')"
          />
        </template>
      </AdminQueueList>

      <AdminFeedbackTable
        v-else
        :items="visible"
        :active-id="cursorId"
        :loading="showSkeleton"
        @activate="emit('activate', $event)"
        @open="emit('open', $event)"
      >
        <template #empty>
          <AdminQueueEmpty
            :title="copy.title"
            :desc="copy.desc"
            :action="copy.action"
            :tone="state === 'error' ? 'error' : 'neutral'"
            @action="emit('action')"
          />
        </template>

        <template #foot>
          <AdminQueueFoot
            :scope="scoped"
            :rows="visible.length"
            :has-prev="hasPrev"
            :has-next="hasNext"
            @prev="emit('prev')"
            @next="emit('next')"
          />
        </template>
      </AdminFeedbackTable>
    </AdminPage>

    <!-- <1280：详情是那个 520px 的抽屉（§4.4「右栏变 520px 抽屉」）。它在两种宽度下
         都是同一个组件：`AdminQueueDetailView` 自己那条 1280 媒体查询按**视口**分档，所以
         抽屉只在视口也小于 1280 时才会走到单栏形态 —— 这也正是它只在窄屏出现的理由。 -->
    <AdminFeedbackDetailDrawerView
      v-if="!isWide"
      :open="detailOpen"
      :item="detail"
      :loading="detailLoading"
      :error="detailError"
      :triage-open="triageOpen"
      :status-items="detailStatusItems"
      :store-error="storeError"
      :assignee-items="assigneeItems"
      :assignee-loading="assigneeLoading"
      :assignee-search="assigneeSearch"
      :assignee-hint="assigneeHint"
      @update:open="emit('update:detailOpen', $event)"
      @update:triage-open="emit('update:triageOpen', $event)"
      @triage="emit('triage', $event)"
      @assign="(id: string, h: string | null) => emit('assign', id, h)"
      @priority="(id: string, v: FeedbackPriority) => emit('priority', id, v)"
      @security="(id: string, v: boolean) => emit('security', id, v)"
      @note="(id: string, body: string) => emit('note', id, body)"
      @update:assignee-search="emit('update:assigneeSearch', $event)"
    />

    <UndoStrip v-if="undo" :message="undo.message" @undo="emit('undo')" @dismiss="emit('dismiss')" />
  </div>
</template>

<style scoped>
.qpage {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

/* 内容列锁 1440（--page-w-admin，由 `AdminPage` 的 `admin` 档给）：16 + 4 + 12 + F +
   16 + 116 + 16 + 88 + 16 + B + 20 = 1440，即 F = 1156 − B（B 是 max-content 的推进
   按钮，56–84 → F ≈ 1072–1100）。F 的下限仍是 740：可用区不足时整行在 `.qlist` 里横着
   滚。居中而不是靠左：这一页的右边没有东西，靠左会让不同视口下的行宽差出一截。 */

/* 无效按键的闪底。0.2s（§7.7 的「出现 / 消失」那一档），中性色 —— 一次落空的按键
   不该借状态三连色里的任何一支说话。类名是容器那一层加上去的（`flashRow` 直接改 DOM），
   而行长在两个列表组件里，所以这一条必须是 `:deep`。 */
.qpage :deep(.qflash) {
  background: var(--fill-2);
  transition: background-color 0.2s ease;
}
</style>
