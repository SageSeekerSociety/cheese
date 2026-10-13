<script setup lang="ts">
// 频道行右边那个折叠加手：这个频道下面挂着的一整块任务，点一下收起来、再点展开。
// 左边那个 ⌄ 管子话题层级，这个管任务块，两个各管各的，所以记号也不一样。
//
// 收起来时它自己顶上：先画这一块的灯（绿 = 这里有活在走，黄 = 有事等你），再画这个
// 频道里进行中的任务数。收起不能把信号一起收掉——否则收起来就成了一条空白，看不出
// 底下到底有没有事。
import type { RowLight } from '@/lib/roomLight'

import { computed } from 'vue'

import TopicRailLight from './TopicRailLight.vue'

import { t } from '@/i18n'
import { countLabel } from '@/lib/topicTree'

const props = defineProps<{
  /** 这一块收起来了没有。 */
  collapsed: boolean
  /** 这个频道里进行中的任务数；收起来时才画。 */
  count?: number
  /** 收起来的那一块该画的灯；不亮是 null。 */
  light?: RowLight | null
}>()

const emit = defineEmits<{ (e: 'toggle'): void }>()

const title = computed(() => t(props.collapsed ? 'work.sidebar.expandTasks' : 'work.sidebar.collapseTasks'))
</script>

<template>
  <span class="task-fold">
    <span v-if="collapsed && (light || count)" class="task-fold__rollup">
      <TopicRailLight v-if="light" :light="light" />
      <span v-if="count" class="task-fold__count" :title="t('work.sidebar.collapsedTasks', { count })">{{
        countLabel(count)
      }}</span>
    </span>
    <button
      type="button"
      class="row-slot task-fold__btn"
      :class="{ 'task-fold__btn--collapsed': collapsed }"
      :title="title"
      :aria-expanded="!collapsed"
      @click.stop="emit('toggle')"
    >
      <v-icon size="15">{{ collapsed ? 'mdi-chevron-right' : 'mdi-chevron-down' }}</v-icon>
    </button>
  </span>
</template>

<style scoped>
/* 把手和它前面那个计数合成一个整体，一起落在行尾。 */
.task-fold {
  flex: none;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.task-fold__rollup {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
/* 进行中的任务数：形态沿用「收起来了多少个」那颗小丸。 */
.task-fold__count {
  flex: none;
  font-size: 12px;
  color: var(--faint);
  background: var(--fill);
  border-radius: 8px;
  padding: 1px 6px;
  font-variant-numeric: tabular-nums;
}
/* 和左边那个子话题开关同一条理由：不给底色 hover，那行底色有三档（静默 / hover /
   选中），任何一个固定的底色 token 都会在其中某一档上糊掉。改成图标本身变深。 */
.task-fold__btn {
  border-radius: var(--radius-sm);
  cursor: pointer;
}
.task-fold__btn :deep(.v-icon) {
  color: var(--faint);
}
.task-fold__btn:hover :deep(.v-icon) {
  color: var(--text);
}
/* 收起来了：这个开关是那一块唯一的入口，让它常亮，别和「平时不亮」混在一档。 */
.task-fold__btn--collapsed :deep(.v-icon) {
  color: var(--muted);
}
</style>
