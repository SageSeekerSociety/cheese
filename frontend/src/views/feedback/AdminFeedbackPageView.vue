<script setup lang="ts">
import type { AdminQueuePageViewProps } from '@/views/admin/AdminQueuePageView.vue'

import { ref } from 'vue'

import AdminQueuePageView from '@/views/admin/AdminQueuePageView.vue'

// `/admin/feedback` 这个旧地址**画的那一半**（§10.2）：它没有自己的画面，整页就是那条
// 队列 —— 把队列的视图原样渲染一遍，props 与事件都由容器那半透下去。
//
// 它和 `/admin/queue` 不是同一棵组件树，所以容器那半自己跑一遍 `useAdminQueue`；两边
// 共用同一个 store，于是照旧是同一份数据（§13 C-10 的理由写在 `AdminQueuePage.vue` 里）。
// 这一件只吃 props、只发事件，老地址这层壳也因此能单独挂起来看。
//
// props 的类型是队列视图自己导出的那一份（`import type` 不算依赖，这一件仍然是 A 级），
// 所以不用把三十条抄一遍 —— 抄的那份迟早和那一件漂开。声明出来有两个好处：容器那几十条
// 绑定重新被类型检查，这一件单独挂起来时 props 也是有名字的。
//
// 模板里的 `{ ...$props, ...$attrs }`：props 走前者，事件（`defineEmits` 没在这儿声明，
// 所以它们都留在 `$attrs` 里）走后者。两边的键不会撞：声明过的 props 不会再出现在
// `$attrs` 里。
//
// 搜索框与详情的落点在最里面那层视图里，容器要能请它们聚焦，所以照原样再往外暴露一层。
defineOptions({ name: 'AdminFeedbackPageView', inheritAttrs: false })

defineProps<AdminQueuePageViewProps>()

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
  <AdminQueuePageView ref="inner" v-bind="{ ...$props, ...$attrs }" />
</template>
