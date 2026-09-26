<script setup lang="ts">
// 进度 —— 上一轮芝士留下的那份清单（进度层，#187），在房间总览里、看板下面。
//
// 它原来挂在对话末尾（「上次的进度」），每来一条消息都压在最下面，把对话往上推；
// 而它回答的是「这个房间做到哪了」，和上面的看板、下面的文档是同一个问题。正在跑
// 的那一轮的清单仍然在对话里，跟着芝士那一行——那一刻它是「它在干什么」。
//
// 和看板一样折成一行摘要（「完成 4/6」），点开是整张清单。一项都没有就整段不画。
import type { TodoItem, Topic } from '../../cx_types'

import { computed, ref, watch } from 'vue'

import { getProgress } from '../../api'

import { t } from '@/i18n'

const props = withDefaults(
  defineProps<{
    topic: Topic | null
    /** 每有一轮动静就加一：一轮结束时清单可能变了。 */
    refreshTick?: number
  }>(),
  { refreshTick: 0 }
)

const items = ref<TodoItem[]>([])
const open = ref(false)

async function load() {
  const tid = props.topic?.id
  if (!tid) {
    items.value = []
    return
  }
  try {
    const progress = await getProgress(tid)
    items.value = progress.items ?? []
  } catch {
    // 进度是背景信息，拿不到就不画，不为它报错。
  }
}

void load()
watch(
  () => props.refreshTick,
  () => void load()
)

const done = computed(() => items.value.filter((i) => i.status === 'completed').length)

// 三态用填充程度递进，一眼可分：空心圈 = 还没做，半填充 = 做到这里，带勾 = 做完了。
function icon(status: TodoItem['status']): string {
  if (status === 'completed') return 'mdi-check-circle'
  if (status === 'in_progress') return 'mdi-circle-slice-4'
  return 'mdi-circle-outline'
}
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
    <ul v-if="open" class="panel-progress__list">
      <li v-for="item in items" :key="item.id" class="progress-item" :class="`progress-item--${item.status}`">
        <v-icon class="progress-item__mark" size="14">{{ icon(item.status) }}</v-icon>
        <span>{{ item.subject }}</span>
      </li>
    </ul>
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
.panel-progress__list {
  margin: 0;
  padding: 0 12px 10px 34px;
  list-style: none;
}
.progress-item {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  padding: 2px 0;
  color: var(--text);
  font-size: 13px;
  line-height: var(--lh-13);
}
/* 图标盒子没有文字基线，整行顶对齐，再把图标压到第一行文字的中线上。 */
.progress-item__mark {
  flex: none;
  margin-top: 2px;
  color: var(--faint);
}
/* 做到这一项：字加深加粗，不用琥珀——这里不是主操作，也不是导航位置。 */
.progress-item--in_progress {
  color: var(--ink);
  font-weight: 600;
}
.progress-item--in_progress .progress-item__mark {
  color: var(--muted);
}
.progress-item--completed {
  color: var(--faint);
}
</style>
