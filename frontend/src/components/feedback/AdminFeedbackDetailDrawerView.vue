<script setup lang="ts">
import type { AdminCandidate } from '@/api'
import type { FeedbackDetail, FeedbackPriority, FeedbackStatus } from '@/cx_types'

import AdminQueueDetailView from '@/components/admin/AdminQueueDetailView.vue'

/**
 * AdminFeedbackDetailDrawerView.vue — 窄屏（<1280px）下详情的那 520px 抽屉（§4.4）
 * **画的那一半**。
 *
 * **这个文件里没有一行详情内容**，它只做三件事：把 520px 那个框画出来、把内容换成
 * `AdminQueueDetailView`、把子组件的事件原样报给调用方。内容写两遍是上一版的坑：
 * 「详情里加一个字段」要改两个文件，而漏掉的那一处只在别人换屏幕时才看得见。
 *
 * 内容**不在这里拉**（id、防抖、状态选项、写操作都在容器那一半）。`Esc` 也**不在这里** ——
 * `v-navigation-drawer` 的 `temporary` 在 Vuetify 3.9 里不会自己关，所以分层后退
 * （先分诊面板、再抽屉）由队列页一处决定，顺序写在那边。
 */
defineOptions({ name: 'AdminFeedbackDetailDrawerView' })

const props = defineProps<{
  open: boolean
  item: FeedbackDetail | null
  loading: boolean
  error: string | null
  triageOpen: boolean
  statusItems: { title: string; value: FeedbackStatus }[]
  storeError: string | null
  assigneeItems: AdminCandidate[]
  assigneeLoading: boolean
  assigneeSearch: string
  assigneeHint: string
}>()

const emit = defineEmits<{
  (e: 'update:open', v: boolean): void
  (e: 'update:triageOpen', v: boolean): void
  (e: 'triage', to: FeedbackStatus): void
  (e: 'assign', id: string, handle: string | null): void
  (e: 'priority', id: string, value: FeedbackPriority): void
  (e: 'security', id: string, value: boolean): void
  (e: 'note', id: string, body: string): void
  (e: 'update:assigneeSearch', v: string): void
}>()

/** 抽屉收起来 = 详情合上。子组件那个「返回」和 Vuetify 自己的 `update:model-value`
 *  都走这一条，两条路合成一句免得两份判断漂开。 */
function close() {
  emit('update:open', false)
}
</script>

<template>
  <v-navigation-drawer
    :model-value="props.open"
    temporary
    location="right"
    width="520"
    class="fb-admin-drawer"
    @update:model-value="(open: boolean) => !open && close()"
  >
    <div class="fb-admin-drawer__inner">
      <AdminQueueDetailView
        :item="props.item"
        :loading="props.loading"
        :error="props.error"
        :triage-open="props.triageOpen"
        :status-items="props.statusItems"
        :store-error="props.storeError"
        :assignee-items="props.assigneeItems"
        :assignee-loading="props.assigneeLoading"
        :assignee-search="props.assigneeSearch"
        :assignee-hint="props.assigneeHint"
        @close="close"
        @update:triage-open="emit('update:triageOpen', $event)"
        @triage="emit('triage', $event)"
        @assign="(id: string, h: string | null) => emit('assign', id, h)"
        @priority="(id: string, v: FeedbackPriority) => emit('priority', id, v)"
        @security="(id: string, v: boolean) => emit('security', id, v)"
        @note="(id: string, body: string) => emit('note', id, body)"
        @update:assignee-search="emit('update:assigneeSearch', $event)"
      />
    </div>
  </v-navigation-drawer>
</template>

<style scoped>
/* 抽屉的内容区自己不领滚动：滚动归 `AdminQueueDetailView` 里那两栏各自领（它们在窄屏下
   是一栏，宽屏下是左栏和右栏各滚各的）。这里只把高度传下去。 */
.fb-admin-drawer__inner {
  height: 100%;
  min-height: 0;
}
</style>
