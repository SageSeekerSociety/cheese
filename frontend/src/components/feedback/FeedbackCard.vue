<script setup lang="ts">
import type { FeedbackCard } from '@/cx_types'

import FeedbackCardView from './FeedbackCardView.vue'

import { useFeedbackStore } from '@/stores/feedback'

// 反馈列表里的一**行**的**取数那一半**。
//
// 画面整个在 `FeedbackCardView.vue`，这里只接反馈 store 做那一件视图不许做的事：点是支持 ——
// `store.toggleSupport` 会发请求，并把服务端回来的**写完之后**的计数写回三份列表。
// 两个文件是一对（形状见 FeedbackCardView.vue 的文件头），分开是为了让几个页面能拿
// 只吃 props 的那一半当普通组件渲染，而「谁能碰 store」留在这一层。
defineOptions({ name: 'FeedbackCard' })

const props = defineProps<{ item: FeedbackCard }>()
const store = useFeedbackStore()
</script>

<template>
  <FeedbackCardView :item="props.item" @support="store.toggleSupport(props.item.id)" />
</template>
