<script setup lang="ts">
// 进度 —— 上一轮芝士留下的那份清单（进度层，#187），在房间总览里、看板下面。
//
// 它原来挂在对话末尾（「上次的进度」），每来一条消息都压在最下面，把对话往上推；
// 而它回答的是「这个房间做到哪了」，和上面的看板、下面的文档是同一个问题。正在跑
// 的那一轮的清单仍然在对话里，跟着芝士那一行——那一刻它是「它在干什么」。
//
// 和看板一样折成一行摘要（「完成 4/6」），点开是整张清单。一项都没有就整段不画。
//
// **只吃 props**：清单在 `components/work/PanelOverviewHost.vue` 里取——先画缓存里上次
// 那份、背后再重取（lib/topicPanelCache.ts），那一层再把结果传下来。这一格只决定画成
// 什么样：`components/panels/**` 下每个 SFC 都是「场景」，场景不取数。
import type { TodoItem } from '../../cx_types'

import { computed, ref } from 'vue'

import TodoChecklist from './TodoChecklist.vue'

import { t } from '@/i18n'

const props = withDefaults(
  defineProps<{
    /** 清单：最新的那一条在最前面。空数组 = 这个房间还没留下过进度，整段不画。 */
    items?: TodoItem[]
  }>(),
  { items: () => [] }
)

const open = ref(false)

const done = computed(() => props.items.filter((i) => i.status === 'completed').length)
</script>

<template>
  <section v-if="items.length" class="panel-progress">
    <button type="button" class="panel-progress__head" :aria-expanded="open" @click="open = !open">
      <v-icon size="16">{{ open ? 'mdi-chevron-down' : 'mdi-chevron-right' }}</v-icon>
      <span class="panel-progress__title t-body">{{ t('work.room.progress.title') }}</span>
      <span class="panel-progress__tally">
        {{ t('work.room.progress.tally', { done, total: items.length }) }}
      </span>
    </button>
    <!-- 和上面看板那一块同一个折法、同一个时长：高度真的变了，一跳的话看不出是这一块
         展开了还是下面的文档自己往下窜了一截。 -->
    <Transition name="progress-fold">
      <div v-if="open" class="progress-fold">
        <div class="progress-fold__clip">
          <div class="panel-progress__list">
            <TodoChecklist :items="items" />
          </div>
        </div>
      </div>
    </Transition>
  </section>
</template>

<style scoped>
.panel-progress {
  flex: 0 0 auto;
  border-bottom: 1px solid var(--line);
}
/* 和看板那一行同一个形状：同高、同内边距、同一个折叠记号。 */
.panel-progress__head {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 8px 12px;
  cursor: pointer;
  text-align: left;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.panel-progress__head:hover {
  background: var(--fill);
}
.panel-progress__title {
  color: var(--ink);
  font-weight: 600;
}
.panel-progress__tally {
  margin-left: auto;
  color: var(--faint);
  font-size: 13px;
  line-height: var(--lh-13);
  font-variant-numeric: tabular-nums;
}
.progress-fold {
  display: grid;
  grid-template-rows: 1fr;
}
.progress-fold-enter-active,
.progress-fold-leave-active {
  transition:
    grid-template-rows 0.2s ease,
    opacity 0.2s ease;
}
.progress-fold-enter-from,
.progress-fold-leave-to {
  grid-template-rows: 0fr;
  opacity: 0;
}
/* 0fr 那一格里它得能缩到 0：内边距放在里面那一层，放在这一层就缩不下去，折叠只剩淡出。 */
.progress-fold__clip {
  min-height: 0;
  overflow: hidden;
}
.panel-progress__list {
  padding: 0 12px 10px 34px;
}
</style>
