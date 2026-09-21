<script setup lang="ts">
import type { FeedbackDetail, FeedbackStatus } from '@/cx_types'

import AdminQueueDetail from '@/components/admin/AdminQueueDetail.vue'

/**
 * AdminFeedbackDetailDrawer.vue — 窄屏（<1280px）下详情的那 520px 抽屉（§4.4）。
 *
 * **这个文件里没有一行详情内容**，它只做三件事：把 520px 那个框画出来、把内容换成
 * `AdminQueueDetail`、把子组件的三个事件原样报给队列页。内容写两遍是上一版的坑：
 * 「详情里加一个字段」要改两个文件，而漏掉的那一处只在别人换屏幕时才看得见。
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
      <AdminQueueDetail
        :item="props.item"
        :loading="props.loading"
        :error="props.error"
        :triage-open="props.triageOpen"
        @close="close"
        @update:triage-open="emit('update:triageOpen', $event)"
        @triage="emit('triage', $event)"
      />
    </div>
  </v-navigation-drawer>
</template>

<style scoped>
/* 抽屉的内容区自己不领滚动：滚动归 `AdminQueueDetail` 里那两栏各自领（它们在窄屏下
   是一栏，宽屏下是左栏和右栏各滚各的）。这里只把高度传下去。 */
.fb-admin-drawer__inner {
  height: 100%;
  min-height: 0;
}
</style>
