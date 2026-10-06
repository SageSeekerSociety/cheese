<template>
  <TopicSelectorView
    :model-value="topics"
    :items="items"
    :loading="isLoading"
    @update:model-value="onTopicsUpdate"
    @search="search"
    @focus="focus"
  />
</template>

<script setup lang="ts">
// 话题选择器**取数的那一半**：搜话题 / 建话题 / 把假项补建成真的都在
// `composables/useTopicSelector` 里，这里只把它和 `TopicSelectorView.vue` 接起来。
//
// 外部接口没变：`v-model` 交话题列表，props 还是 `max`（历史上就没用上）与
// `defaultTopics`（输入框空着时先摆出来的那几项）。
import type { Topic } from '@/types'

import { useTopicSelector } from '@/composables/useTopicSelector'

import TopicSelectorView from './TopicSelectorView.vue'

const props = withDefaults(
  defineProps<{
    max?: number
    defaultTopics?: Topic[]
  }>(),
  {
    max: -1,
    defaultTopics: () => [],
  }
)

const topics = defineModel<Topic[]>({ default: () => [] })

const { items, isLoading, search, focus, resolveTopics } = useTopicSelector({
  defaultTopics: () => props.defaultTopics,
})

async function onTopicsUpdate(newTopics: Topic[]) {
  // 先乐观写回，再拿补建之后的最终列表覆盖一次。
  topics.value = newTopics
  topics.value = await resolveTopics(newTopics)
}
</script>
