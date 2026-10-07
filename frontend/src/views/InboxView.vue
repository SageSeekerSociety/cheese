<script setup lang="ts">
import type { WaitingItem } from '@/cx_types'

import { computed, onMounted, ref } from 'vue'

import { useNewProjectDialog } from '@/composables/useNewProjectDialog'

import { listAwaitingMe, markAllAlertsRead, markRead, resolveAlert } from '@/api'
import BaseButton from '@/components/base/BaseButton.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import AppPage from '@/components/common/AppPage.vue'
import NavLink from '@/components/common/NavLink.vue'
import { t } from '@/i18n'
import { relTime } from '@/lib/relTime'
import { DEFAULT_SHELL, termParams } from '@/lib/shell'
import { taskTitle, topicTitle } from '@/lib/topicState'
import { useWorkspaceStore } from '@/stores/workspace'
import JoinSpaceDialog from '@/views/home/JoinSpaceDialog.vue'
import NotificationFeed from '@/views/home/NotificationFeed.vue'

// 「待办」：首页那一格点开就是这一页（手机上是底栏的一格）。
//
// 上面是**等你处理**：「需要我处理」只在这一处。和任务列表读同一份规则（后端
// `room_task/presentation.py`），范围换成我能看见的全部项目，再按「这件事点的是谁」过滤。
// 它答的是「现在还没处理完的有哪些」，处理完就消失。按项目分组；每一件先说要你做什么，
// 再说是哪件事、等的是什么（提问的原话、改动的主题、停住的原因）。要你拍板的那几件，
// 选项就摆在这一行上；芝士写给你的变更提醒读过就点「标为已读」。
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

/** 这一件要你做什么。负责人那一条按停在哪一格说：文档写好了等你开始、检查没过、被退回。 */
function askOf(item: WaitingItem): { text: string; tone: 'warn' | 'danger' } {
  if (item.reason === 'reviewer') return { text: t('home.inbox.reason.reviewer'), tone: 'warn' }
  if (item.reason === 'reporter') return { text: t('home.inbox.reason.reporter'), tone: 'warn' }
  if (item.reason === 'asked') return { text: t('home.inbox.reason.asked'), tone: 'warn' }
  if (item.reason === 'decide') return { text: t('home.inbox.reason.decide'), tone: 'warn' }
  if (item.reason === 'read') return { text: t('home.inbox.reason.read'), tone: 'warn' }
  if (item.phrase === 'checks_failed') return { text: t('home.inbox.reason.checksFailed'), tone: 'danger' }
  if (item.phrase === 'bounced') return { text: t('home.inbox.reason.bounced'), tone: 'warn' }
  return { text: t('home.inbox.reason.start'), tone: 'warn' }
}

/** 任务的事写任务的名字，频道自己的事写频道的；来自通知的写通知的标题。 */
function itemTitle(item: WaitingItem): string {
  if (item.alertId != null && item.headline) return item.headline
  if (item.taskTitle) return taskTitle({ title: item.taskTitle, title_source: item.taskTitleSource })
  return topicTitle({ title: item.topicTitle })
}

/** 标题里人写的那一段：没起名时那一行写的是界面的「新任务」，不算。 */
function itemTitleWritten(item: WaitingItem): string {
  if (item.alertId != null && item.headline) return item.headline
  return item.taskTitle ?? item.topicTitle
}

function linkTo(item: WaitingItem) {
  const query = item.blockId ? { block: item.blockId } : undefined
  // 任务的事打开任务页，不是它所在的频道。
  if (item.taskId) {
    return {
      name: 'workspace-task',
      params: { projectId: item.projectId, topicId: item.topicId, taskId: item.taskId },
      query,
    }
  }
  // 芝士在支线里问的，打开那条支线。
  if (item.threadId) {
    return {
      name: 'workspace-thread',
      params: { projectId: item.projectId, topicId: item.topicId, threadId: item.threadId },
      query,
    }
  }
  // 不指向哪个频道的通知，打开它所在项目的总览。
  if (!item.topicId) return { name: 'workspace-overview', params: { projectId: item.projectId } }
  return { name: 'workspace-topic', params: { projectId: item.projectId, topicId: item.topicId }, query }
}

const byProject = computed(() => {
  const groups: { projectId: string; projectName: string; items: WaitingItem[] }[] = []
  for (const item of items.value) {
    const group = groups.find((g) => g.projectId === item.projectId)
    if (group) group.items.push(item)
    else groups.push({ projectId: item.projectId, projectName: item.projectName, items: [item] })
  }
  return groups
})

// 拍板、标为已读：交上去，这一件随之从清单上消失。
const acting = ref<number | null>(null)
const actionError = ref('')
async function settle(item: WaitingItem, send: (alertId: number) => Promise<unknown>) {
  if (item.alertId == null) return
  acting.value = item.alertId
  actionError.value = ''
  try {
    await send(item.alertId)
    items.value = items.value.filter((other) => other.alertId !== item.alertId)
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : t('home.inbox.actionFailed')
  } finally {
    acting.value = null
  }
}
const decide = (item: WaitingItem, chosen: string) => settle(item, (id) => resolveAlert(id, chosen))
const dismiss = (item: WaitingItem) => settle(item, markRead)

// 一个项目里攒了好几条变更提醒时，一下全部标为已读，不用一条一条点。
const unreadIn = (group: { items: WaitingItem[] }) => group.items.filter((item) => item.reason === 'read').length
const clearing = ref<string | null>(null)
async function dismissAll(projectId: string) {
  clearing.value = projectId
  actionError.value = ''
  try {
    await markAllAlertsRead(projectId)
    items.value = items.value.filter((item) => item.projectId !== projectId || item.reason !== 'read')
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : t('home.inbox.actionFailed')
  } finally {
    clearing.value = null
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
    <template v-else>
      <p v-if="actionError" role="alert" class="inbox__error t-meta">{{ actionError }}</p>
      <section v-for="group in byProject" :key="group.projectId" class="inbox__project">
        <div class="inbox__project-head">
          <h3 class="inbox__project-name">
            <span data-user-content>{{ group.projectName }}</span>
            <span class="inbox__count">{{ group.items.length }}</span>
          </h3>
          <BaseButton
            v-if="unreadIn(group) > 1"
            size="sm"
            :loading="clearing === group.projectId"
            @click="dismissAll(group.projectId)"
          >
            {{ t('home.inbox.markAllRead') }}
          </BaseButton>
        </div>
        <ul class="inbox__list">
          <li
            v-for="item in group.items"
            :key="`${item.topicId}:${item.taskId ?? ''}:${item.blockId ?? ''}:${item.alertId ?? ''}`"
            class="inbox-item"
          >
            <NavLink :to="linkTo(item)" class="inbox-item__link">
              <span class="inbox-item__head">
                <span class="inbox-item__ask" :class="`inbox-item__ask--${askOf(item).tone}`">{{
                  askOf(item).text
                }}</span>
                <span class="inbox-item__title t-body" :data-user-content="itemTitleWritten(item) || undefined">{{
                  itemTitle(item)
                }}</span>
              </span>
              <span v-if="item.detail" class="inbox-item__detail t-body" data-user-content>{{ item.detail }}</span>
              <span v-if="item.topicId" class="inbox-item__where" :data-user-content="item.topicTitle || undefined">
                # {{ topicTitle({ title: item.topicTitle }) }} · {{ relTime(item.at) }}
              </span>
              <span v-else class="inbox-item__where">{{ relTime(item.at) }}</span>
            </NavLink>
            <div v-if="item.reason === 'decide' && item.options?.length" class="inbox-item__options">
              <BaseButton
                v-for="option in item.options"
                :key="option"
                size="sm"
                kind="secondary"
                :loading="acting === item.alertId"
                @click="decide(item, option)"
              >
                {{ option }}
              </BaseButton>
            </div>
            <div v-else-if="item.reason === 'read'" class="inbox-item__options">
              <BaseButton size="sm" kind="secondary" :loading="acting === item.alertId" @click="dismiss(item)">
                {{ t('home.inbox.markRead') }}
              </BaseButton>
            </div>
          </li>
        </ul>
      </section>
    </template>

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
.inbox__error {
  margin: 0 0 8px;
  color: var(--danger-ink);
}
.inbox__project {
  margin-bottom: 24px;
}
.inbox__project-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-height: 32px;
  padding: 0 4px 4px;
  border-bottom: 1px solid var(--line-2);
}
.inbox__project-name {
  margin: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  font-weight: 600;
  color: var(--ink);
}
.inbox__count {
  color: var(--faint);
  font-weight: 400;
}
.inbox__list {
  margin: 0;
  padding: 0;
  list-style: none;
}
.inbox-item {
  border-bottom: 1px solid var(--line);
}
.inbox-item__link {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 14px 4px;
  color: inherit;
  text-decoration: none;
}
.inbox-item__link:hover {
  background: var(--fill);
}
.inbox-item__head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  min-width: 0;
}
.inbox-item__ask {
  flex-shrink: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  font-weight: 600;
}
.inbox-item__ask--warn {
  color: var(--accent-ink);
}
.inbox-item__ask--danger {
  color: var(--danger-ink);
}
.inbox-item__title {
  min-width: 0;
  overflow: hidden;
  color: var(--ink);
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.inbox-item__detail {
  display: -webkit-box;
  overflow: hidden;
  color: var(--text);
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}
.inbox-item__where {
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--faint);
}
.inbox-item__options {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 0 4px 14px;
}
</style>
