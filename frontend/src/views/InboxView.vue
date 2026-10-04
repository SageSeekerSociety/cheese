<script setup lang="ts">
import type { WaitingItem } from '@/cx_types'

import { computed, onMounted, ref } from 'vue'

import { useNewProjectDialog } from '@/composables/useNewProjectDialog'

import { listAwaitingMe } from '@/api'
import BaseButton from '@/components/base/BaseButton.vue'
import AppPage from '@/components/common/AppPage.vue'
import { t } from '@/i18n'
import { phraseLabel } from '@/lib/board'
import { DEFAULT_SHELL, termParams } from '@/lib/shell'
import { taskTitle, topicTitle } from '@/lib/topicState'
import { useWorkspaceStore } from '@/stores/workspace'
import JoinSpaceDialog from '@/views/home/JoinSpaceDialog.vue'
import NotificationFeed from '@/views/home/NotificationFeed.vue'

// 「待办」：首页那一格点开就是这一页（手机上是底栏的一格）。
//
// 上面是**等你处理**：和看板读同一份规则（后端 `room_task/presentation.py`），范围换成
// 我能看见的全部项目，再按「这件事点的是谁」过滤。它答的是「现在还没处理完的有哪些」，
// 处理完就消失。
//
// 下面是**动态**：提到你、回复你、邀请你、截止提醒。它们是一条条事件，读过就算。以前
// 它们在顶栏的铃铛里；铃铛拆了，两样东西放在同一页，人回来只看这一处。
defineOptions({ name: 'InboxView' })

const items = ref<WaitingItem[]>([])
const loading = ref(true)
const failed = ref(false)

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

const store = useWorkspaceStore()
onMounted(() => {
  void load()
  if (!store.projectsSettled) void store.refreshProjects()
})

// 一个项目都没有的人（刚注册）：这一页给他唯一有意义的两步。
const noProjects = computed(() => store.projectsSettled && store.projects.length === 0)
const projectTerm = termParams(DEFAULT_SHELL)
const { show: showNewProjectDialog } = useNewProjectDialog()
const joinOpen = ref(false)

const REASON: Record<WaitingItem['reason'], string> = {
  reviewer: 'home.inbox.reason.reviewer',
  reporter: 'home.inbox.reason.reporter',
  asked: 'home.inbox.reason.asked',
}

/** 活的事写活的名字，房间自己的事写房间的；还没起名的按读者的语言说。 */
function itemTitle(item: WaitingItem): string {
  if (item.taskTitle) return taskTitle({ title: item.taskTitle, title_source: item.taskTitleSource })
  return topicTitle({ title: item.topicTitle, title_source: item.topicTitleSource })
}

function linkTo(item: WaitingItem) {
  return {
    name: 'workspace-topic',
    params: { projectId: item.projectId, topicId: item.topicId },
    query: item.blockId ? { block: item.blockId } : undefined,
  }
}
</script>

<template>
  <AppPage :title="t('navigation.inbox')">
    <section v-if="noProjects" class="inbox__start">
      <p class="t-title">{{ t('work.emptyTitle', projectTerm) }}</p>
      <div class="inbox__start-actions">
        <BaseButton kind="primary" prepend-icon="mdi-plus" @click="showNewProjectDialog()">
          {{ t('navigation.newProject', projectTerm) }}
        </BaseButton>
        <BaseButton kind="secondary" prepend-icon="mdi-ticket-confirmation-outline" @click="joinOpen = true">
          {{ t('work.joinAction') }}
        </BaseButton>
      </div>
    </section>

    <h2 class="inbox__heading">{{ t('home.inbox.waiting') }}</h2>
    <div v-if="loading" class="inbox__quiet">
      <v-progress-circular indeterminate size="18" width="2" />
    </div>
    <div v-else-if="failed" class="inbox__quiet">
      <span>{{ t('home.inbox.loadFailed') }}</span>
      <BaseButton kind="secondary" size="sm" @click="load">{{ t('home.inbox.retry') }}</BaseButton>
    </div>
    <p v-else-if="items.length === 0" class="inbox__quiet">{{ t('home.inbox.waitingEmpty') }}</p>
    <v-list v-else class="inbox__list" bg-color="transparent" lines="two">
      <v-list-item
        v-for="item in items"
        :key="`${item.topicId}:${item.taskId ?? ''}:${item.blockId ?? ''}`"
        :to="linkTo(item)"
        class="inbox-item"
      >
        <template #prepend>
          <span class="inbox-item__mark" />
        </template>
        <v-list-item-title class="inbox-item__title">
          {{ itemTitle(item) }}
        </v-list-item-title>
        <v-list-item-subtitle class="inbox-item__meta">
          {{ t(REASON[item.reason]) }} · {{ phraseLabel(item.phrase) }} · {{ item.projectName }}
        </v-list-item-subtitle>
      </v-list-item>
    </v-list>

    <NotificationFeed />
    <JoinSpaceDialog v-model="joinOpen" />
  </AppPage>
</template>

<style scoped>
.inbox__start {
  margin-top: 24px;
  padding: 24px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: var(--surface);
}
.inbox__start-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 16px;
}
.inbox__heading {
  margin: 24px 0 8px;
  font-size: 14px;
  font-weight: 600;
  color: var(--ink);
}
.inbox__quiet {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 44px;
  margin: 0;
  padding: 0 16px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: var(--surface);
  color: var(--faint);
}
.inbox__list {
  padding: 0;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: var(--surface);
  overflow: hidden;
}
.inbox-item {
  border-bottom: 1px solid var(--line);
}
.inbox-item :deep(.v-list-item__prepend) {
  width: auto;
  margin-inline-end: 12px;
}
.inbox-item:last-child {
  border-bottom: none;
}
/* 待处理是整个产品里要人动手的那一列，和看板上同一个暖色的标记。 */
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
