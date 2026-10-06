<script setup lang="ts">
import { ref } from 'vue'

import AdminQueuePageView from '@/views/admin/AdminQueuePageView.vue'

// `/admin/feedback` 这个旧地址**画的那一半**（§10.2）：它没有自己的画面，整页就是那条
// 队列 —— 把队列的视图原样渲染一遍，props 与事件都由容器那半透下去。
//
// 它和 `/admin/queue` 不是同一棵组件树，所以容器那半自己跑一遍 `useAdminQueue`；两边
// 共用同一个 store，于是照旧是同一份数据（§13 C-10 的理由写在 `AdminQueuePage.vue` 里）。
// 这一件只吃 props、只发事件，老地址这层壳也因此能单独挂起来看。
//
// 透传用 `v-bind="$attrs"`：它没有自己的 props 要声明，而 props 与事件一共五六十条，
// 抄一遍只会多一处会漂开的地方。搜索框与详情的落点在最里面那层视图里，容器要能请它们
// 聚焦，所以照原样再往外暴露一层。
defineOptions({ name: 'AdminFeedbackPageView', inheritAttrs: false })

const inner = ref<InstanceType<typeof AdminQueuePageView> | null>(null)

function focusSearch() {
  inner.value?.focusSearch()
}

function focusAssignee(): boolean {
  return inner.value?.focusAssignee() ?? false
}

defineExpose({ focusSearch, focusAssignee })
</script>

<template>
  <AdminQueuePageView ref="inner" v-bind="$attrs" />
</template>
