<!--
  The rendering half of AdminQueuePage.vue, split out by `pnpm run scene:split`: props in,
  events out. The page keeps the address, the requests and the stores.

  这一份收着队列屏的**版式与 DOM 约定**：`.qpage` 那一列（详情与队列二选一、工具行、
  两个列表视图、窄屏抽屉、撤销条都在这一列里），以及三个跨组件的 DOM 约定 ——
  `data-owns-arrow-keys`（哪一片子树自己收方向键）、`fbrow-*` / `ftrow-*` 生成的 **id**、
  无效按键的 `.qflash` 闪底。前两个是选择器和事件分发的落点，第三个是 `:deep` 才够得着
  的行内样式，所以这三样都必须跟版式住在一起：分开写的话，改一处不会提醒你另一处。

  这里**不知道**「手上这一条是谁」—— 光标、已读、撤销栈、详情防抖全在调用方
  （`useAdminQueue`）。这一层只把收到的值画出来，把人的动作原样报回去。
-->

<script setup lang="ts">
import type { AdminCandidate } from '@/api'
import type { QueueView, QueueWindowKey } from '@/composables/useAdminQueue'
import type { FeedbackCard, FeedbackDetail, FeedbackPriority, FeedbackStatus } from '@/cx_types'
import type { AdminTab } from '@/stores/feedback'

import { ref } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminFeedbackTable from '@/components/admin/AdminFeedbackTable.vue'
import AdminPage from '@/components/admin/AdminPage.vue'
import AdminQueueDetail from '@/components/admin/AdminQueueDetail.vue'
import AdminQueueFoot from '@/components/admin/AdminQueueFoot.vue'
import AdminQueueList from '@/components/admin/AdminQueueList.vue'
import AdminQueueEmpty from '@/components/admin/queue/AdminQueueEmpty.vue'
import AdminQueueHeader from '@/components/admin/queue/AdminQueueHeader.vue'
import AdminQueueToolbar from '@/components/admin/queue/AdminQueueToolbar.vue'
import UndoStrip from '@/components/admin/UndoStrip.vue'

defineOptions({ name: 'AdminQueuePageView' })

const props = withDefaults(
  defineProps<{
    /** 视口 ≥1280：详情整页接管，不再和队列并排。 */
    isWide: boolean
    detailOpen: boolean
    detail: FeedbackDetail | null
    detailLoading: boolean
    detailError: string | null
    /** 详情里写操作失败的原话（指派 / 优先级 / 安全问题 / 备注）。 */
    detailWriteError?: string
    triageOpen: boolean
    /** 服务端 meta 里那份状态梯子；缺省由详情自己兜底。 */
    statuses?: FeedbackStatus[]
    /** 「指派给谁」的候选与那串搜索词。 */
    candidates?: AdminCandidate[]
    search?: string
    searching?: boolean
    unread: number
    view: QueueView
    lane: AdminTab
    lanes: { value: AdminTab; label: string }[]
    query: string
    chips: { key: QueueWindowKey; text: string; clearAria: string }[]
    status: FeedbackStatus | 'all'
    statusOptions: { value: FeedbackStatus | 'all'; label: string }[]
    showScopeNote: boolean
    items: FeedbackCard[]
    cursorId: string | null
    cursorIndex: number
    loading: boolean
    showStatusWord: boolean
    /** 四态文案（加载 / 空 / 错误 / 筛空）与那一格的颜色。 */
    emptyTitle: string
    emptyDesc?: string
    emptyAction: string
    emptyTone: 'error' | 'neutral'
    scope: boolean
    rows: number
    hasPrev: boolean
    hasNext: boolean
    undo: { message: string } | null
  }>(),
  {
    detailWriteError: '',
    statuses: undefined,
    candidates: () => [],
    search: '',
    searching: false,
    emptyDesc: undefined,
  }
)

const emit = defineEmits<{
  'close-detail': []
  'update:detailOpen': [v: boolean]
  'update:triageOpen': [v: boolean]
  triage: [to: FeedbackStatus]
  assign: [id: string, handle: string | null]
  priority: [id: string, priority: FeedbackPriority]
  security: [id: string, security: boolean]
  note: [id: string, body: string]
  search: [v: string]
  'mark-read': []
  refresh: []
  'update:view': [v: QueueView]
  'update:query': [v: string]
  'update:status': [v: FeedbackStatus | 'all']
  'select-lane': [lane: AdminTab]
  'clear-window': [key: QueueWindowKey]
  'update:activeIndex': [v: number]
  advance: [id: string]
  activate: [id: string]
  open: [id: string]
  action: []
  prev: []
  next: []
  undo: []
  dismiss: []
}>()

const { t } = useI18n()

/** 搜索框住在 `AdminQueueToolbar` 里，而 `/` 键和清窗口之后都要把焦点送进去。
 *  位置是那一件的事、时机是调用方的事，所以这里只转一道手。 */
const toolbarRef = ref<InstanceType<typeof AdminQueueToolbar> | null>(null)
const detailRef = ref<InstanceType<typeof AdminQueueDetail> | null>(null)

/** `A` 键要落到的那个输入框（§8）。宽屏那一档详情就在这一层里，直接问它；
 *  窄屏那一档在抽屉里，退一步在 DOM 上找 —— 抽屉里只有这一颗 combobox。 */
function focusAssignee() {
  if (detailRef.value?.focusAssignee) {
    detailRef.value.focusAssignee()
    return
  }
  document.querySelector<HTMLInputElement>('.v-navigation-drawer .v-field input')?.focus()
}

defineExpose({ focusSearch: () => toolbarRef.value?.focusSearch(), focusAssignee })
</script>

<template>
  <div class="qpage">
    <!-- 宽屏的详情是**整页接管**（§4.4：440 左栏 + 760 右栏）。队列行本身
         就要 1100px，两个并排在任何常见视口里都塞不下 —— 塞得下的那种宽度下，
         `AdminQueueDetail` 自己那条 1280 媒体查询又已经把它拆成两栏了。 -->
    <AdminQueueDetail
      v-if="props.isWide && props.detailOpen"
      ref="detailRef"
      :item="props.detail"
      :loading="props.detailLoading"
      :error="props.detailError"
      :triage-open="props.triageOpen"
      :statuses="props.statuses"
      :write-error="props.detailWriteError"
      :candidates="props.candidates"
      :search="props.search"
      :searching="props.searching"
      @close="emit('close-detail')"
      @update:triage-open="emit('update:triageOpen', $event)"
      @triage="emit('triage', $event)"
      @assign="(id, handle) => emit('assign', id, handle)"
      @priority="(id, priority) => emit('priority', id, priority)"
      @security="(id, security) => emit('security', id, security)"
      @note="(id, body) => emit('note', id, body)"
      @search="emit('search', $event)"
    />

    <AdminPage v-else :title="t('navigation.admin.queue')" :sub="t('feedback.queue.sub')">
      <template #tools>
        <AdminQueueHeader
          :unread="props.unread"
          :view="props.view"
          @mark-read="emit('mark-read')"
          @refresh="emit('refresh')"
          @update:view="emit('update:view', $event)"
        />
      </template>

      <AdminQueueToolbar
        ref="toolbarRef"
        :lane="props.lane"
        :lanes="props.lanes"
        :query="props.query"
        :chips="props.chips"
        :status="props.status"
        :status-options="props.statusOptions"
        :show-scope-note="props.showScopeNote"
        @update:query="emit('update:query', $event)"
        @update:status="emit('update:status', $event)"
        @select-lane="emit('select-lane', $event)"
        @clear-window="emit('clear-window', $event)"
      />

      <!-- 队列与总表**同时只挂一个**（§8 末：视图切换时解绑），否则同一个 `j` 会被两个
             `role="grid"` 各收一次。 -->
      <AdminQueueList
        v-if="props.view === 'list'"
        :items="props.items"
        :active-index="props.cursorIndex"
        :loading="props.loading"
        :show-status-word="props.showStatusWord"
        @update:active-index="emit('update:activeIndex', $event)"
        @advance="emit('advance', $event)"
        @open="emit('open', $event)"
      >
        <template #empty>
          <AdminQueueEmpty
            :title="props.emptyTitle"
            :desc="props.emptyDesc"
            :action="props.emptyAction"
            :tone="props.emptyTone"
            @action="emit('action')"
          />
        </template>

        <template #foot>
          <!-- 脚的内容两个视图共用一份（`AdminQueueFoot`），壳在各自的列表组件里。 -->
          <AdminQueueFoot
            :scope="props.scope"
            :rows="props.rows"
            :has-prev="props.hasPrev"
            :has-next="props.hasNext"
            @prev="emit('prev')"
            @next="emit('next')"
          />
        </template>
      </AdminQueueList>

      <AdminFeedbackTable
        v-else
        :items="props.items"
        :active-id="props.cursorId"
        :loading="props.loading"
        @activate="emit('activate', $event)"
        @open="emit('open', $event)"
      >
        <template #empty>
          <AdminQueueEmpty
            :title="props.emptyTitle"
            :desc="props.emptyDesc"
            :action="props.emptyAction"
            :tone="props.emptyTone"
            @action="emit('action')"
          />
        </template>

        <template #foot>
          <AdminQueueFoot
            :scope="props.scope"
            :rows="props.rows"
            :has-prev="props.hasPrev"
            :has-next="props.hasNext"
            @prev="emit('prev')"
            @next="emit('next')"
          />
        </template>
      </AdminFeedbackTable>
    </AdminPage>

    <!-- <1280：详情是那个 520px 的抽屉（§4.4「右栏变 520px 抽屉」）。它在两种宽度下
         都是同一个组件：`AdminQueueDetail` 自己那条 1280 媒体查询按**视口**分档，所以
         抽屉只在视口也小于 1280 时才会走到单栏形态 —— 这也正是它只在窄屏出现的理由。

         `Esc` **不在这里**。`v-navigation-drawer` 的 `temporary` 在 Vuetify 3.9 里
         不会自己关，所以这一层不跟任何人抢事件：分层后退（先分诊面板、再抽屉）由调用方
         一处决定，顺序写在那边。 -->
    <v-navigation-drawer
      v-if="!props.isWide"
      :model-value="props.detailOpen"
      temporary
      location="right"
      width="520"
      class="fb-admin-drawer"
      @update:model-value="(open: boolean) => !open && emit('update:detailOpen', false)"
    >
      <div class="fb-admin-drawer__inner">
        <AdminQueueDetail
          :item="props.detail"
          :loading="props.detailLoading"
          :error="props.detailError"
          :triage-open="props.triageOpen"
          :statuses="props.statuses"
          :write-error="props.detailWriteError"
          :candidates="props.candidates"
          :search="props.search"
          :searching="props.searching"
          @close="emit('update:detailOpen', false)"
          @update:triage-open="emit('update:triageOpen', $event)"
          @triage="emit('triage', $event)"
          @assign="(id, handle) => emit('assign', id, handle)"
          @priority="(id, priority) => emit('priority', id, priority)"
          @security="(id, security) => emit('security', id, security)"
          @note="(id, body) => emit('note', id, body)"
          @search="emit('search', $event)"
        />
      </div>
    </v-navigation-drawer>

    <UndoStrip v-if="props.undo" :message="props.undo.message" @undo="emit('undo')" @dismiss="emit('dismiss')" />
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
   不该借状态三连色里的任何一支说话。类名是调用方加上去的（`flashRow` 直接改 DOM），
   而行长在两个列表组件里，所以这一条必须是 `:deep`。 */
.qpage :deep(.qflash) {
  background: var(--fill-2);
  transition: background-color 0.2s ease;
}

/* 抽屉的内容区自己不领滚动：滚动归 `AdminQueueDetail` 里那两栏各自领（它们在窄屏下
   是一栏，宽屏下是左栏和右栏各滚各的）。这里只把高度传下去。 */
.fb-admin-drawer__inner {
  height: 100%;
  min-height: 0;
}
</style>
