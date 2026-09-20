<script setup lang="ts">
import type { WaitingItem } from '@/cx_types'

import { computed, onMounted, ref } from 'vue'

import { listAwaitingMe } from '@/api'

// 「待办」——手机底栏三格之一（设计见 docs/plans/2026-08-18-mobile-shell-design.md）。
//
// 它列的是**待我处理**：和看板读同一份规则（后端 `room_task/presentation.py`），
// 范围换成我能看见的全部项目，再按「这件事点的是谁」过滤。它此前接的是顶栏铃铛那份
// 通知数据（提及与回复）——通知是一条条事件记录，答不出「现在还没处理完的有哪些」：
// 一张验收卡被驳回之后，它那条通知还在。
//
// 铃铛照旧：通知负责把人叫回来，这一格负责他回来之后不用自己翻。
defineOptions({ name: 'InboxView' })

const items = ref<WaitingItem[]>([])
const loading = ref(true)
const failed = ref(false)

const REASON_TEXT: Record<WaitingItem['reason'], string> = {
  reviewer: '待你验收',
  reporter: '你提的需求已有交付',
  asked: '待你回答',
}

async function load() {
  loading.value = true
  failed.value = false
  try {
    items.value = (await listAwaitingMe()).data
  } catch {
    failed.value = true
  } finally {
    loading.value = false
  }
}

onMounted(load)

const empty = computed(() => !loading.value && !failed.value && items.value.length === 0)

function linkTo(item: WaitingItem) {
  return {
    name: 'workspace-topic',
    params: { projectId: item.projectId, topicId: item.topicId },
  }
}
</script>

<template>
  <div class="inbox-page">
    <div v-if="loading" class="inbox-page__state">
      <v-progress-circular indeterminate size="24" width="2" color="primary" />
    </div>

    <div v-else-if="failed" class="inbox-page__state">
      <span class="t-body">未能读取待处理事项</span>
      <v-btn variant="text" color="primary" size="small" @click="load">重试</v-btn>
    </div>

    <div v-else-if="empty" class="inbox-page__state">
      <span class="t-body inbox-page__empty">暂无待处理事项</span>
    </div>

    <v-list v-else class="inbox-page__list" bg-color="transparent" lines="two">
      <v-list-item
        v-for="item in items"
        :key="`${item.topicId}:${item.taskId ?? ''}`"
        :to="linkTo(item)"
        class="inbox-item"
      >
        <template #prepend>
          <span class="inbox-item__mark" />
        </template>
        <v-list-item-title class="inbox-item__title">
          {{ item.taskTitle || item.topicTitle }}
        </v-list-item-title>
        <v-list-item-subtitle class="inbox-item__meta">
          {{ REASON_TEXT[item.reason] }} · {{ item.displayStatus }} · {{ item.projectName }}
        </v-list-item-subtitle>
      </v-list-item>
    </v-list>
  </div>
</template>

<style scoped>
.inbox-page {
  height: 100%;
  overflow-y: auto;
  background: var(--surface);
}

.inbox-page__state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 48px 16px;
  color: var(--muted);
}

.inbox-page__empty {
  color: var(--faint);
}

.inbox-page__list {
  padding: 0;
}

.inbox-item {
  border-bottom: 1px solid var(--line);
}

/* 待处理是整个产品里唯一需要人动手的那一列，和看板上同一个暖色的标记。 */
.inbox-item__mark {
  width: 6px;
  height: 6px;
  border-radius: var(--radius-pill);
  background: var(--warn);
}

.inbox-item__title {
  color: var(--ink);
  font-size: 14px;
}

.inbox-item__meta {
  color: var(--muted);
  font-size: 13px;
}
</style>
