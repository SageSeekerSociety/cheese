<script setup lang="ts">
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'

// 队列那两个列表 `empty` 插槽里画的四态块。列表和总表各有一个插槽，内容却是同一份，
// 所以在拆的时候收成了这一件 —— 两处各写一遍的话，「错误态多一样东西」这种事只会被
// 改到其中一边。
//
// 它只认五样：一句标题、一句为什么、至多一个动作、一个语气，以及**服务端那句原话**。
// 前四样交给共用的 `BaseEmptyState`（形状和看板、模型页的空态一致）；原话挂在外面
// 那层 `title` 上——**不能挂在组件上**：`title` 是 `BaseEmptyState` 自己声明的 prop，
// 传下去会被当成标题，画的就成了服务端那句原文。块里只写「读失败」，具体为什么读失败
// 要能问出来，所以它得是一个真的元素。
defineOptions({ name: 'AdminQueueEmpty' })

withDefaults(
  defineProps<{
    title: string
    desc?: string
    /** 动作文案。空串（空态那两档）不画按钮 —— 空态没有可点的东西。 */
    action?: string
    tone?: 'neutral' | 'error'
    /** 读失败时服务端那句原话；别的三态是 `null`。 */
    raw?: string | null
  }>(),
  { desc: undefined, action: '', tone: 'neutral', raw: null }
)

const emit = defineEmits<{ action: [] }>()
</script>

<template>
  <div class="qpage__state-raw" :title="raw || undefined">
    <BaseEmptyState :title="title" :desc="desc" :action="action || undefined" :tone="tone" @action="emit('action')" />
  </div>
</template>

<style scoped>
/* 四态块的位置由两个列表组件给（`.qlist__none-box` / `.aft__none` 那 96px 顶距和
   320px 宽），字和按钮在 `BaseEmptyState` 里。这一层只剩外面那圈：`title` 上挂着
   服务端原话，所以它得是一个真的元素。 */
.qpage__state-raw {
  display: block;
}
</style>
