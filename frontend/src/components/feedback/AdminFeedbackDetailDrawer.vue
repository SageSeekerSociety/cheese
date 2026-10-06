<script setup lang="ts">
import type { FeedbackDetail, FeedbackPriority, FeedbackStatus } from '@/cx_types'

import { computed } from 'vue'

import { useAdminAssigneeSearch } from '@/composables/useAdminAssigneeSearch'

import AdminFeedbackDetailDrawerView from '@/components/feedback/AdminFeedbackDetailDrawerView.vue'
import { allStatuses, statusMeta } from '@/lib/feedbackMeta'
import { useFeedbackStore } from '@/stores/feedback'

/**
 * AdminFeedbackDetailDrawer.vue — 窄屏（<1280px）下详情的那 520px 抽屉（§4.4）
 * **取数的那一半**（容器）。
 *
 * 画面整个搬去了 `AdminFeedbackDetailDrawerView.vue`（只吃 props、只发事件）；这一件
 * 留下取数：状态梯子的选项按服务端 meta 算、四路写操作（指派 / 优先级 / 安全问题 /
 * 内部备注）落 store、「指派给谁」那个下拉的搜索。抽屉本身只是一只框，不添内容。
 *
 * 内容**不在这里拉**。id 与防抖（F-06 的 150ms + `detailSeq`）住在队列页 ——
 * 「手上这一条是谁」要同时看列表光标和抽屉开关，是页面级的判断；放这一层会变成
 * 第二个「当前是哪条」，而两份答案迟早不一样。
 *
 * `Esc` 也**不在这里**。`v-navigation-drawer` 的 `temporary` 在 Vuetify 3.9 里
 * （`node_modules/vuetify/lib/components/VNavigationDrawer/VNavigationDrawer.js`，
 * 那里没有任何 `Escape` 监听）**不会**自己关，所以这一层不跟任何人抢事件：分层后退
 * （先分诊面板、再抽屉）由队列页一处决定，顺序写在那边。
 */
defineOptions({ name: 'AdminFeedbackDetailDrawer' })

const props = defineProps<{
  open: boolean
  item: FeedbackDetail | null
  loading: boolean
  error: string | null
  triageOpen: boolean
}>()

const emit = defineEmits<{
  (e: 'update:open', v: boolean): void
  (e: 'update:triageOpen', v: boolean): void
  (e: 'triage', to: FeedbackStatus): void
}>()

const store = useFeedbackStore()
const { search, candidates, searching, hint } = useAdminAssigneeSearch()

/** 状态选项用**服务端**那份（meta 里的 `statuses`）：梯子四级，外加梯子之外的「不修复」。 */
const statusItems = computed(() =>
  allStatuses(store.meta?.statuses).map((s) => ({ title: statusMeta(s).label, value: s }))
)

/** 「指派给谁」那个下拉的输入串。它是组合式函数里的一个 ref，视图只往上报新值。 */
function setSearch(v: string) {
  search.value = v
}

/** 抽屉收起来 = 详情合上。子组件那个「返回」和 Vuetify 自己的 `update:model-value`
 *  都走这一条，两条路合成一句免得两份判断漂开。 */
function close() {
  emit('update:open', false)
}

function onAssign(id: string, handle: string | null) {
  void store.assign(id, handle)
}

function onPriority(id: string, value: FeedbackPriority) {
  void store.setPriority(id, value)
}

function onSecurity(id: string, value: boolean) {
  void store.setSecurity(id, value)
}

function onNote(id: string, body: string) {
  void store.addNote(id, body)
}
</script>

<template>
  <AdminFeedbackDetailDrawerView
    :open="props.open"
    :item="props.item"
    :loading="props.loading"
    :error="props.error"
    :triage-open="props.triageOpen"
    :status-items="statusItems"
    :store-error="store.error"
    :assignee-items="candidates"
    :assignee-loading="searching"
    :assignee-search="search"
    :assignee-hint="hint"
    @update:open="close"
    @update:triage-open="emit('update:triageOpen', $event)"
    @triage="emit('triage', $event)"
    @assign="onAssign"
    @priority="onPriority"
    @security="onSecurity"
    @note="onNote"
    @update:assignee-search="setSearch"
  />
</template>
