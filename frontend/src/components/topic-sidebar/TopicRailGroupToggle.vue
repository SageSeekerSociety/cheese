<script setup lang="ts">
// 一组被收起来的话题的组头：chevron + 组名 + 条数（+ 收着的时候「那边有动静」的点）。
//
// 两处用它：话题列表里的「其他话题」和列表底部的「已归档」。同一条侧栏里「一组被
// 收起来的话题」只能有一种读法，所以它是一个组件，而不是两段长得像的模板。
// 组头长这样，不代表**组里的行**也换一种形态——那些行走的还是完整的 `.topic-row`。
import TopicRailBadge from './TopicRailBadge.vue'

defineProps<{
  label: string
  count: number
  open: boolean
  /** 收着的时候有没有新消息（展开着就画行上的角标，不在这儿再点一次）。 */
  unread: boolean
  /** 那颗点 hover 时说的话（「其他话题里有新消息」/「归档话题里有新消息」）。 */
  unreadTitle: string
  /** 「已归档」那一个多带一个类，测试和页面样式凭它区分两个组头。 */
  archived?: boolean
}>()

const emit = defineEmits<{ (e: 'toggle'): void }>()
</script>

<template>
  <button type="button" class="group-toggle" :class="{ 'archived-toggle': archived }" @click="emit('toggle')">
    <v-icon size="15" class="c-faint">
      {{ open ? 'mdi-chevron-down' : 'mdi-chevron-right' }}
    </v-icon>
    <span class="t-eyebrow">{{ label }}</span>
    <span class="group-count">{{ count }}</span>
    <TopicRailBadge v-if="!open && unread" dot :title="unreadTitle" />
  </button>
</template>

<style scoped>
/* 收起来的一组话题的组头：底部的「已归档」，以及话题列表里的「其他话题」。
   组头长在 <v-list> **外面**：`.v-list--nav` 自带 8px 的 padding-inline，组头搁在
   列表里就会比列表外的那个组头右移 8px——两个同款组头一上一下差着一级缩进，
   「其他话题」读起来像上一条话题的子项。 */
.group-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  width: calc(100% - 16px);
  margin: 2px 8px;
  padding: 6px 8px;
  border-radius: 8px;
  cursor: pointer;
  text-align: left;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.group-toggle:hover {
  background: var(--fill);
}
/* 组名的字号来自 .t-eyebrow（全局），这里只把它自己的内边距收掉。 */
.group-toggle .t-eyebrow {
  padding: 0;
}
.group-count {
  font-size: 12px;
  color: var(--faint);
  background: var(--fill);
  border-radius: 8px;
  padding: 1px 6px;
}
</style>
