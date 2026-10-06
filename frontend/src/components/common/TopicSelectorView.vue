<template>
  <v-autocomplete
    v-model:search="topicInput"
    autocomplete="off"
    :model-value="modelValue"
    :items="items"
    :loading="loading"
    :label="t('shell.topicSelector.label')"
    :placeholder="t('shell.topicSelector.placeholder')"
    variant="outlined"
    item-title="name"
    item-value="id"
    chips
    closable-chips
    multiple
    return-object
    :no-filter="true"
    hide-no-data
    auto-select-first
    @update:model-value="$emit('update:modelValue', $event)"
    @update:search="onSearchUpdate"
    @focus="$emit('focus', topicInput)"
  >
    <template #chip="{ props, item }">
      <v-chip v-bind="props" :text="item.raw.name"></v-chip>
    </template>

    <template #item="{ props, item }">
      <v-list-item
        v-if="item.raw.isFakeItem"
        v-bind="props"
        :title="t('questions.ask.buttons.createTopic', { name: item.raw.name })"
        prepend-icon="mdi-plus"
      ></v-list-item>
      <v-list-item v-else v-bind="props" :title="item.raw.name"></v-list-item>
    </template>
  </v-autocomplete>
</template>

<script setup lang="ts">
// 话题选择器**画的那一半**：只认 props、只发事件。搜话题 / 建话题 / 把假项补建成
// 真的都在 `composables/useTopicSelector` 里；只吃 props 的它因此能在只装
// Vuetify + i18n 的树里单独渲染。
//
// 输入框里当前那串字（`topicInput`）是这一层的 UI 状态：`update:search` 时既更新它、
// 又往上发一份 `search`，聚焦时把当前值一并带给父级（父级据此决定要不要拉默认项）。
import type { TopicOption } from '@/composables/useTopicSelector'
import type { Topic } from '@/types'

import { ref } from 'vue'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

withDefaults(
  defineProps<{
    modelValue: Topic[]
    items: TopicOption[]
    loading?: boolean
  }>(),
  { loading: false }
)

const emit = defineEmits<{
  'update:modelValue': [topics: Topic[]]
  search: [value: string]
  focus: [value: string]
}>()

const topicInput = ref('')

function onSearchUpdate(value: string) {
  topicInput.value = value
  emit('search', value)
}
</script>
