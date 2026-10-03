<script setup lang="ts">
// 归档去向：列表最底下那一组「已归档」。归档了的话题离开活跃的树落在这里（新的
// 在前），做完的活因此不再挤占侧栏。
//
// 收起来的开关是这个组件自己的状态：它从来没落过盘（刷一次就回到收着），所以不必
// 从外面递进来。**没有归档话题时整个组件什么都不画**——条件留在这里（而不是让
// 父级 v-if），是为了那一个开关不随「最后一条也被取消归档」而复位。
import type { Topic } from '@/cx_types'

import { computed, ref } from 'vue'

import VirtualList from '../common/VirtualList.vue'

import TopicRailBadge from './TopicRailBadge.vue'
import TopicRailGroupToggle from './TopicRailGroupToggle.vue'

import { t } from '@/i18n'
import { topicTitle } from '@/lib/topicState'
import { kindLabel } from '@/lib/topicTree'

const props = defineProps<{
  rows: Topic[]
  selectedTopicId: string | null
  /** 话题列表那一层的滚动容器（`.rail-scroll`）。归档组就长在它底下，行太多时按它的
   *  窗口算要挂几行；不给就整列画（和以前一样）。 */
  scrollParent?: HTMLElement | null
  /** 整页形态（手机）：行更高、取消归档那颗按钮要撑到手指点得中。 */
  page: boolean
  /** 组头收起来时那颗点要不要画（归档话题里有新消息）。 */
  unread: boolean
  /** 按 id 问某一行有几条未读。 */
  unreadOf: (id: string) => number
}>()

const emit = defineEmits<{
  (e: 'select-topic', id: string): void
  (e: 'hover-topic', id: string): void
  (e: 'leave-topic'): void
  (e: 'unarchive-topic', id: string): void
}>()

const open = ref(false)

// 归档是会越堆越多的（做过一轮又一轮的活都落在这儿），所以这一组也照话题组那套走：
// 行的身份、以及选中的那一行要留在 DOM 里（光标可能正停在它上面）。参数按 `unknown`
// 收：这根函数是被 `item-key` 接过去的，那边只保证「给你一样东西」，具体是什么自己认。
const rowKey = (topic: unknown): string => (topic as Topic).id
const keepMounted = computed<readonly number[] | undefined>(() => {
  const id = props.selectedTopicId
  if (!id) return undefined
  const index = props.rows.findIndex((topic) => topic.id === id)
  return index >= 0 ? [index] : undefined
})
</script>

<template>
  <template v-if="rows.length">
    <TopicRailGroupToggle
      :label="t('work.sidebar.archived')"
      :count="rows.length"
      :open="open"
      :unread="unread"
      :unread-title="t('work.sidebar.archivedUnread')"
      archived
      @toggle="open = !open"
    />
    <v-list v-if="open" density="compact" nav class="py-0" tabindex="-1">
      <!-- Archived rows are ordered newest first and only pile up over time, so past
           VIRTUAL_LIST_THRESHOLD (lib/virtualList.ts) the group keeps only the rows in view
           mounted; the scroll parent is the topic rail itself (`.rail-scroll`), since the
           group lives in that flow. Below the threshold this is the plain list it always
           was — the same rows, in the same order. -->
      <VirtualList
        :items="rows"
        :item-key="rowKey"
        :scroll-parent="scrollParent"
        :estimated-size="36"
        :buffer-size="320"
        :keep-mounted="keepMounted"
      >
        <template #item="{ item }">
          <v-list-item
            tabindex="0"
            :active="item.id === selectedTopicId"
            rounded="lg"
            :data-row-actions="item.id"
            class="topic-row topic-row--archived"
            :class="{ 'is-active': item.id === selectedTopicId }"
            @click="emit('select-topic', item.id)"
            @mouseenter="emit('hover-topic', item.id)"
            @mouseleave="emit('leave-topic')"
          >
            <template #prepend>
              <v-icon size="16" class="me-1 c-faint" icon="mdi-archive-outline" />
            </template>
            <v-list-item-title class="d-flex align-center ga-2 topic-title">
              <span class="text-truncate" :data-user-content="item.title || undefined">{{ topicTitle(item) }}</span>
              <span class="kind-text">{{ kindLabel(item) }}</span>
            </v-list-item-title>
            <template #append>
              <TopicRailBadge v-if="unreadOf(item.id) > 0" class="me-1" :count="unreadOf(item.id)" />
              <!-- eslint-disable-next-line vue/no-restricted-syntax -- nav bar button whose look this component styles exactly (design-system §3.6 exception) -->
              <v-btn
                v-if="item.can_archive"
                icon="mdi-archive-arrow-up-outline"
                size="small"
                variant="text"
                color="on-surface-variant"
                density="comfortable"
                :title="t('work.room.menu.unarchive')"
                class="split-btn"
                :class="{ 'tap-target': page }"
                @click.stop="emit('unarchive-topic', item.id)"
              />
            </template>
          </v-list-item>
        </template>
      </VirtualList>
    </v-list>
  </template>
</template>

<style scoped>
/* 归档行读起来是「做完了的」：标题压暗一档。 */
.topic-row--archived :deep(.v-list-item-title) {
  color: var(--muted);
}
/* 下面这一小段是 `.topic-row` 这个形状的底子：位置、行高、Vuetify 那套内边距和
   行盒。它和 `TopicRailRow.vue` 里那份是同一套规则，**有意各写一份**——scoped
   CSS 只作用于自己这个组件，而归档行不是 `TopicRailRow`（它的槽里是归档图标，
   行尾是「取消归档」，没有状态点和折叠开关）。代价是这两处会同时改；收益是
   两个组件都能单独渲染（/demo/catalog），不靠对方在场。 */
.topic-row {
  position: relative;
  min-height: 36px;
  margin-block: 2px;
}
.topic-row :deep(.v-list-item__content) {
  padding-block: 0;
}
/* 行盒必须跟着字号一起给：nav 变体把它钉在 1rem，而 14px 的 CJK 字身比 16px 的
   行盒还高，标题自带的 overflow: hidden 会把下伸的字母下缘切平。 */
.topic-row :deep(.v-list-item-title) {
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--text);
}
.topic-title {
  color: var(--text);
}
/* Vuetify 的 prepend spacer 默认 ~32px，压到 8px 才和话题行的图标列成列。 */
.topic-row :deep(.v-list-item__spacer) {
  width: 8px !important;
}
.topic-row :deep(.v-list-item__prepend) {
  align-items: center;
}
/* 取消归档按钮与 ⋯ 同属一个按钮家族：同样的 25px 方盒、7px 圆角、16px 图标、
   琥珀强调 + hover 反馈，避免归档区里出现一颗尺寸/配色不一致的按钮。 */
.split-btn {
  width: 25px;
  height: 25px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  color: var(--accent);
}
.split-btn :deep(.v-icon) {
  font-size: 16px;
}
.split-btn:hover {
  background: var(--fill);
  color: var(--accent);
}
/* 整页形态：手指点的地方至少 44px 高。 */
.topic-rail--page .topic-row {
  min-height: 44px;
}
</style>
