<template>
  <v-autocomplete
    v-model:search="topicInput"
    autocomplete="off"
    :model-value="topics"
    :items="addTopicItems"
    :loading="isLoading"
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
    @update:model-value="onTopicsUpdate"
    @update:search="fetchTopics"
    @focus="onFocus"
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

<script lang="ts" setup>
// 话题选择器「画 + 交互」的那一半：搜什么、选了什么、要不要新建，全在这里。真正的
// 网络调用（搜索、新建话题）不在这里——由容器通过 `searchTopics` / `createTopic`
// 两个回调 prop 传进来，这个组件在原时机调用，行为不变。
//
// 分家的理由和 components/common/UserRef.vue 一样：原件自己 import 了 @/network/api/tags，
// 于是每一个装着它的场景都被拖进「必须连 API 层」，架构指标掉到 C。把它拆成「只认
// props 的展示件」，场景里就能单独渲染它，其它使用方（Ask、AdminMembersPage）继续用
// 原来那只自给自足的 TopicSelector.vue，一行不动。
import type { Topic } from '@/types'

import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { debounce } from 'lodash-es'

const props = withDefaults(
  defineProps<{
    max?: number
    defaultTopics?: Topic[]
    /** 搜索话题，返回候选列表（含已有的话题）。 */
    searchTopics: (query: string) => Promise<{ id: number; name: string }[]>
    /** 按名字新建一个话题，返回新话题的 id。 */
    createTopic: (name: string) => Promise<number>
  }>(),
  {
    max: -1,
    defaultTopics: () => [],
  }
)

const { t } = useI18n()

const topics = defineModel<Topic[]>({ default: () => [] })

const topicInput = ref('')
const isLoading = ref(false)
const addTopicItems = ref<
  {
    id: number
    name: string
    isFakeItem?: boolean
  }[]
>([])

const onTopicsUpdate = async (newTopics: Topic[]) => {
  // Optimistic update
  topics.value = newTopics

  // Check if any topics need creation (id === -1)
  // We use type assertion since the fake item comes from addTopicItems which has extra props
  const hasFake = newTopics.some((t: any) => t.id === -1)
  if (!hasFake) return

  const finalTopics = [...newTopics]
  let changed = false

  for (let i = 0; i < finalTopics.length; i++) {
    const topic = finalTopics[i] as any
    if (topic.id === -1) {
      try {
        isLoading.value = true
        const newId = await props.createTopic(topic.name)
        // successful creation, replace with real topic (stripping isFakeItem)
        finalTopics[i] = { id: newId, name: topic.name }
        changed = true
      } catch (error) {
        console.error('Create topic failed', error)
        // If creation failed, remove it from list
        finalTopics.splice(i, 1)
        i--
        changed = true
      } finally {
        isLoading.value = false
      }
    }
  }

  if (changed) {
    topics.value = finalTopics
  }
}

const fetchTopics = debounce(async (value: string) => {
  const q = value?.trim()
  if (!q) {
    addTopicItems.value = [...props.defaultTopics]
    return
  }

  try {
    isLoading.value = true
    const result = await props.searchTopics(q)

    const items: { id: number; name: string; isFakeItem?: boolean }[] = [...result]
    // Add create option if it doesn't strictly match existing
    if (!items.find((i) => i.name === q)) {
      items.push({
        id: -1,
        name: q,
        isFakeItem: true,
      })
    }

    addTopicItems.value = items
  } catch (error) {
    console.error('获取话题失败:', error)
    // On error, still allow creating?
    addTopicItems.value = [{ id: -1, name: q, isFakeItem: true }]
  } finally {
    isLoading.value = false
  }
}, 300)

const onFocus = () => {
  if (!topicInput.value) {
    fetchTopics('')
  }
}
</script>

<style lang="scss">
// Removed custom styles as v-autocomplete handles layout
</style>
