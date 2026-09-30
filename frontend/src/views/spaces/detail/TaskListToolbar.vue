<script setup lang="ts">
// 题目列表上方那一条：范围、话题、搜索、排序。它们说的都是「这张列表里放哪些、怎么排」，
// 所以放在同一行；「发布题目」是这一页要做的事，在页头，不在这里。
//
// 「我发布的」走另一个接口（带审核状态），它不按话题筛、不按这里的排序排，所以选了它
// 这两个下拉就收起来，换成「只看待处理」。
import type { Topic } from '@/types'
import type { TaskScope, TaskSortKey } from './taskListFilters'

import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import SegmentedControl from '@/components/common/SegmentedControl.vue'

const props = defineProps<{
  scope: TaskScope
  pendingOnly: boolean
  topics: Topic[]
  selectedTopics: number[]
  sort: TaskSortKey
  search: string
}>()

const emit = defineEmits<{
  'update:scope': [scope: TaskScope]
  'update:pendingOnly': [on: boolean]
  'update:selectedTopics': [ids: number[]]
  'update:sort': [sort: TaskSortKey]
  search: [keywords: string]
}>()

const { t } = useI18n()

const scopeOptions = computed(() =>
  (['all', 'participating', 'publishing'] as const).map((value) => ({
    value,
    label: t(`spaces.detail.tasks.scope.${value}`),
  }))
)

const sortKeys: TaskSortKey[] = ['latestPublished', 'latestUpdated', 'nearestDeadline']

// 按钮上写选了什么：一个也没选是「全部话题」，选了一个写它的名字，多了写个数。
const topicLabel = computed(() => {
  const picked = props.topics.filter((topic) => props.selectedTopics.includes(topic.id))
  if (!props.selectedTopics.length) return t('spaces.detail.tasks.allTopics')
  if (props.selectedTopics.length === 1 && picked.length === 1) return picked[0].name
  return t('spaces.detail.tasks.topicCount', { n: props.selectedTopics.length })
})

const toggleTopic = (id: number) => {
  const next = props.selectedTopics.includes(id)
    ? props.selectedTopics.filter((picked) => picked !== id)
    : [...props.selectedTopics, id]
  emit('update:selectedTopics', next)
}

// 搜索按回车才生效：边打边查会把半个词发给后端。
const draft = ref(props.search)
watch(
  () => props.search,
  (value) => (draft.value = value)
)
</script>

<template>
  <div class="tlt">
    <SegmentedControl
      :model-value="scope"
      :options="scopeOptions"
      :label="t('spaces.detail.tasks.scopeLabel')"
      size="md"
      @update:model-value="emit('update:scope', $event)"
    />

    <button
      v-if="scope === 'publishing'"
      type="button"
      class="tlt__field tlt__toggle"
      :class="{ 'tlt__toggle--on': pendingOnly }"
      :aria-pressed="pendingOnly"
      data-testid="pending-only"
      @click="emit('update:pendingOnly', !pendingOnly)"
    >
      <v-icon size="16">{{ pendingOnly ? 'mdi-checkbox-marked' : 'mdi-checkbox-blank-outline' }}</v-icon>
      {{ t('spaces.detail.tasks.pendingOnly') }}
    </button>

    <v-menu v-else :close-on-content-click="false" location="bottom start">
      <template #activator="{ props: menu }">
        <button
          v-bind="menu"
          type="button"
          class="tlt__field tlt__drop"
          :class="{ 'tlt__drop--set': selectedTopics.length }"
          data-testid="topic-filter"
        >
          <span class="tlt__drop-label">{{ topicLabel }}</span>
          <v-icon size="16">mdi-chevron-down</v-icon>
        </button>
      </template>
      <v-list density="compact" class="tlt__menu" :aria-label="t('spaces.detail.tasks.topic')">
        <v-list-item v-if="!topics.length" :title="t('spaces.detail.tasks.noTopics')" disabled />
        <v-list-item
          v-for="topic in topics"
          :key="topic.id"
          role="menuitemcheckbox"
          :aria-checked="selectedTopics.includes(topic.id)"
          :title="topic.name"
          @click="toggleTopic(topic.id)"
        >
          <template #prepend>
            <v-icon size="18">
              {{ selectedTopics.includes(topic.id) ? 'mdi-checkbox-marked' : 'mdi-checkbox-blank-outline' }}
            </v-icon>
          </template>
        </v-list-item>
        <template v-if="selectedTopics.length">
          <v-divider />
          <v-list-item :title="t('spaces.detail.tasks.clearTopics')" @click="emit('update:selectedTopics', [])" />
        </template>
      </v-list>
    </v-menu>

    <form class="tlt__field tlt__search" role="search" @submit.prevent="emit('search', draft.trim())">
      <v-icon size="16">mdi-magnify</v-icon>
      <input
        v-model="draft"
        type="search"
        :placeholder="t('spaces.detail.tasks.searchPlaceholder')"
        :aria-label="t('spaces.detail.tasks.searchPlaceholder')"
        autocomplete="off"
      />
    </form>

    <v-menu v-if="scope !== 'publishing'" location="bottom end">
      <template #activator="{ props: menu }">
        <button v-bind="menu" type="button" class="tlt__field tlt__drop" data-testid="sort">
          <v-icon size="16">mdi-sort-variant</v-icon>
          <span class="tlt__drop-label">{{ t(`spaces.detail.tasks.sortOptions.${sort}`) }}</span>
          <v-icon size="16">mdi-chevron-down</v-icon>
        </button>
      </template>
      <v-list density="compact" :aria-label="t('spaces.detail.tasks.sort')">
        <v-list-item
          v-for="key in sortKeys"
          :key="key"
          :title="t(`spaces.detail.tasks.sortOptions.${key}`)"
          :active="key === sort"
          @click="emit('update:sort', key)"
        />
      </v-list>
    </v-menu>
  </div>
</template>

<style scoped>
.tlt {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
.tlt__field {
  display: inline-flex;
  flex: none;
  gap: 6px;
  align-items: center;
  height: 32px;
  padding: 0 10px;
  color: var(--text);
  font: inherit;
  font-size: 13px;
  line-height: var(--lh-13);
  white-space: nowrap;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  transition: border-color var(--dur-quick) var(--ease-standard);
}
.tlt__field .v-icon {
  color: var(--faint);
}
button.tlt__field {
  cursor: pointer;
}
button.tlt__field:hover {
  border-color: var(--faint);
}
.tlt__field:focus-visible,
.tlt__search:focus-within {
  outline: 2px solid var(--focus-ring);
  outline-offset: 1px;
}
.tlt__drop-label {
  max-width: 160px;
  overflow: hidden;
  text-overflow: ellipsis;
}
.tlt__drop--set,
.tlt__toggle--on {
  color: var(--ink);
  font-weight: 600;
}
.tlt__toggle--on .v-icon {
  color: var(--ink);
}
.tlt__search {
  flex: 1 1 200px;
  min-width: 160px;
  color: var(--faint);
}
.tlt__search input {
  flex: 1;
  min-width: 0;
  color: var(--ink);
  outline: none;
}
.tlt__menu {
  max-height: 360px;
  overflow-y: auto;
}
</style>
