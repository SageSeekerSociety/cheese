<script setup lang="ts">
import type { FeedbackDetail, FeedbackPriority, FeedbackStatus } from '@/cx_types'

import { computed, ref } from 'vue'

import { useAdminAssigneeSearch } from '@/composables/useAdminAssigneeSearch'

import AdminQueueDetailView from '@/components/admin/AdminQueueDetailView.vue'
import { allStatuses, statusMeta } from '@/lib/feedbackMeta'
import { useFeedbackStore } from '@/stores/feedback'

/**
 * AdminQueueDetail.vue — 一条反馈的详情（§4.4）**取数的那一半**（容器）。
 *
 * 画面整个搬去了 `AdminQueueDetailView.vue`（只吃 props、只发事件）；这一件留下取数：
 * 状态梯子的选项按服务端 meta 算、四路写操作（指派 / 优先级 / 安全问题 / 内部备注）
 * 落 store、「指派给谁」那个下拉的搜索。三种断点共用同一个视图片段，理由写在视图里。
 *
 * **内容不在这里拉。** id 与防抖归调用方（F-06 的 150ms + `detailSeq` 在队列页），
 * 因为「手上这一条是谁」是页面级的判断（列表光标、抽屉开关都算进去），放在这一层会
 * 变成第二个「当前是哪条」。这一件只画调用方递进来的 `item`。
 *
 * `focusAssignee` 从这里再往外转一层（`A` 键要落到那个输入框）：真正的输入框在视图里，
 * 而「什么时候该请它聚焦」由队列页决定，所以这个入口一路往外暴露到页面。
 *
 * 写操作分两路：**状态**从 `triage` 出去（调用方要记撤销条，那是页面级的栈），
 * **指派 / 优先级 / 安全问题 / 内部备注**直接落到 store —— 它们没有撤销条（撤销是给
 * 单键误触的保险，而这几样都要先点开面板、再选一个值）。
 */
defineOptions({ name: 'AdminQueueDetail' })

const props = defineProps<{
  item: FeedbackDetail | null
  loading: boolean
  /** 拉取失败时的原话。为空表示这一趟没出错。 */
  error: string | null
  /** 分诊面板展开着没有。见文件开头：状态在调用方。 */
  triageOpen: boolean
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'update:triageOpen', v: boolean): void
  (e: 'triage', to: FeedbackStatus): void
}>()

const store = useFeedbackStore()
const { search, candidates, searching, hint } = useAdminAssigneeSearch()

/** 状态选项用**服务端**那份（meta 里的 `statuses`）：梯子四级，外加梯子之外的「不修复」。 */
const statusItems = computed(() =>
  allStatuses(store.meta?.statuses).map((s) => ({ title: statusMeta(s).label, value: s }))
)

const detailView = ref<InstanceType<typeof AdminQueueDetailView> | null>(null)

/** 「指派给谁」那个下拉的输入串。它是组合式函数里的一个 ref，视图只往上报新值。 */
function setSearch(v: string) {
  search.value = v
}

function focusAssignee() {
  detailView.value?.focusAssignee()
}

defineExpose({ focusAssignee })

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
  <AdminQueueDetailView
    ref="detailView"
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
    @close="emit('close')"
    @update:triage-open="emit('update:triageOpen', $event)"
    @triage="emit('triage', $event)"
    @assign="onAssign"
    @priority="onPriority"
    @security="onSecurity"
    @note="onNote"
    @update:assignee-search="setSearch"
  />
</template>
