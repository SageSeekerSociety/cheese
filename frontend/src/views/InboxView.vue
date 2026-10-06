<script setup lang="ts">
import type { WaitingItem } from '@/cx_types'

import { computed, onMounted, ref } from 'vue'

import { useNewProjectDialog } from '@/composables/useNewProjectDialog'

import { listAwaitingMe } from '@/api'
import BaseButton from '@/components/base/BaseButton.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
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
const errorReason = ref('')

async function load() {
  loading.value = true
  failed.value = false
  errorReason.value = ''
  try {
    items.value = (await listAwaitingMe()).data
  } catch (e) {
    failed.value = true
    errorReason.value = e instanceof Error ? e.message : ''
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
  return topicTitle({ title: item.topicTitle })
}

function linkTo(item: WaitingItem) {
  // 芝士在支线里问的，打开那条支线。
  if (item.threadId) {
    return {
      name: 'workspace-thread',
      params: { projectId: item.projectId, topicId: item.topicId, threadId: item.threadId },
      query: item.blockId ? { block: item.blockId } : undefined,
    }
  }
  return {
    name: 'workspace-topic',
    params: { projectId: item.projectId, topicId: item.topicId },
    query: item.blockId ? { block: item.blockId } : undefined,
  }
}
</script>

<template>
  <AppPage :title="t('navigation.inbox')">
    <!-- 零项目时这一页就是新用户的落点（`landingForMember` 把他放在这儿）。原来只有
         一句「暂无{project}」加两颗按钮，说的是「这里什么都没有」，没告诉他平台上有
         哪些路可走——团队和项目的入口分别在别的地方，他看不到。现在拆成三条并列的
         起步路，每条一句话说清进去能得到什么。 -->
    <section v-if="noProjects" class="inbox__start">
      <p class="t-title">{{ t('work.emptyTitle', projectTerm) }}</p>
      <p class="inbox__start-lede">{{ t('work.startPaths.lede') }}</p>

      <ul class="inbox__paths">
        <li class="inbox__path">
          <v-icon icon="mdi-folder-plus-outline" size="20" class="inbox__path-icon" />
          <div class="inbox__path-text">
            <span class="inbox__path-title">{{ t('work.startPaths.project.title', projectTerm) }}</span>
            <span class="inbox__path-body">{{ t('work.startPaths.project.body') }}</span>
          </div>
          <BaseButton kind="primary" size="sm" @click="showNewProjectDialog()">
            {{ t('navigation.newProject', projectTerm) }}
          </BaseButton>
        </li>

        <li class="inbox__path">
          <v-icon icon="mdi-ticket-confirmation-outline" size="20" class="inbox__path-icon" />
          <div class="inbox__path-text">
            <span class="inbox__path-title">{{ t('work.startPaths.invite.title') }}</span>
            <span class="inbox__path-body">{{ t('work.startPaths.invite.body') }}</span>
          </div>
          <BaseButton kind="secondary" size="sm" @click="joinOpen = true">
            {{ t('work.startPaths.invite.action') }}
          </BaseButton>
        </li>

        <li class="inbox__path">
          <v-icon icon="mdi-account-group-outline" size="20" class="inbox__path-icon" />
          <div class="inbox__path-text">
            <span class="inbox__path-title">{{ t('work.startPaths.teams.title') }}</span>
            <span class="inbox__path-body">{{ t('work.startPaths.teams.body') }}</span>
          </div>
          <BaseButton kind="secondary" size="sm" :to="{ name: 'HomeTeamsExplore' }">
            {{ t('work.startPaths.teams.action') }}
          </BaseButton>
        </li>
      </ul>

      <p class="inbox__start-note">{{ t('work.startPaths.settingsNote') }}</p>
    </section>

    <h2 class="inbox__heading">{{ t('home.inbox.waiting') }}</h2>
    <div v-if="loading" class="inbox__quiet">
      <v-progress-circular indeterminate size="18" width="2" />
    </div>
    <!-- Items waiting on you failed to load: replace this block in place with an
         error and a retry (docs/design-system.md §3.10), the one failure look the
         whole product shares, not this page's own bare text and button. -->
    <BaseLoadError
      v-else-if="failed"
      class="inbox__load-error"
      :title="t('home.inbox.loadFailed')"
      :error="errorReason || null"
      @retry="load"
    />
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
.inbox__start-lede {
  margin: 4px 0 0;
  color: var(--muted);
  font-size: 13px;
}
.inbox__paths {
  margin: 16px 0 0;
  padding: 0;
  list-style: none;
}
.inbox__path {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 0;
  border-top: 1px solid var(--line);
}
.inbox__path:first-child {
  border-top: none;
  padding-top: 0;
}
.inbox__path-icon {
  color: var(--muted);
  flex: none;
}
.inbox__path-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  flex: 1;
}
.inbox__path-title {
  color: var(--ink);
  font-size: 14px;
  font-weight: 500;
}
.inbox__path-body {
  color: var(--muted);
  font-size: 13px;
}
.inbox__start-note {
  margin: 16px 0 0;
  padding-top: 12px;
  border-top: 1px solid var(--line);
  color: var(--faint);
  font-size: 12px;
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
.inbox__load-error {
  padding: 8px 0;
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
