<template>
  <AppPage :title="t('navigation.inbox')">
    <!-- 一个项目都没有的人：新账号登录时已经有了自己的第一个项目（`landingForMember`），
         走到这里的是后来退出或删光了项目的人。三条起步路并列，每条一句话说清进去能
         得到什么。 -->
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
          <BaseButton kind="primary" size="sm" @click="emit('new-project')">
            {{ t('navigation.newProject', projectTerm) }}
          </BaseButton>
        </li>

        <li class="inbox__path">
          <v-icon icon="mdi-ticket-confirmation-outline" size="20" class="inbox__path-icon" />
          <div class="inbox__path-text">
            <span class="inbox__path-title">{{ t('work.startPaths.invite.title') }}</span>
            <span class="inbox__path-body">{{ t('work.startPaths.invite.body') }}</span>
          </div>
          <BaseButton kind="secondary" size="sm" @click="emit('join')">
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
      @retry="emit('reload')"
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
            @click="emit('dismiss-all', group.projectId)"
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
                @click="emit('decide', item, option)"
              >
                {{ option }}
              </BaseButton>
            </div>
            <div
              v-else-if="item.reason === 'decide' && item.alertId != null"
              class="inbox-item__options inbox-item__answer"
            >
              <v-text-field
                v-model="answers[item.alertId]"
                variant="outlined"
                density="compact"
                hide-details
                autocomplete="off"
                :aria-label="t('home.inbox.answerLabel')"
                :placeholder="t('home.inbox.answerLabel')"
                @keydown.enter.prevent="answer(item)"
              />
              <BaseButton
                size="sm"
                kind="secondary"
                :disabled="!answers[item.alertId]?.trim()"
                :loading="acting === item.alertId"
                @click="answer(item)"
              >
                {{ t('home.inbox.answer') }}
              </BaseButton>
            </div>
            <div v-else-if="item.reason === 'read'" class="inbox-item__options">
              <BaseButton size="sm" kind="secondary" :loading="acting === item.alertId" @click="emit('dismiss', item)">
                {{ t('home.inbox.markRead') }}
              </BaseButton>
            </div>
          </li>
        </ul>
      </section>
    </template>

    <!-- The activity feed and its dialog are drawn right here: both take props only. The
         requests behind the feed and behind answering an invitation, which bucket is
         remembered, and the join dialog's state all live in the container. -->
    <NotificationFeed
      :filter="feedFilter"
      :counts="feedCounts"
      :notifications="feedNotifications"
      :loading="feedLoading"
      :failed="feedFailed"
      :more-failed="feedMoreFailed"
      :error-reason="feedErrorReason"
      :has-more="feedHasMore"
      :fresh="feedFresh"
      :answering="answering"
      @update:filter="emit('update:feed-filter', $event)"
      @mark-all-read="emit('feed-mark-all-read')"
      @mark-as-read="emit('feed-mark-read', $event)"
      @delete="emit('feed-delete', $event)"
      @more="emit('feed-more')"
      @retry="emit('feed-retry')"
      @show-fresh="emit('feed-show-fresh')"
    />

    <JoinSpaceDialog
      :model-value="joinOpen"
      :busy="joinBusy"
      :error="joinError"
      @update:model-value="emit('update:join-open', $event)"
      @submit="emit('join-submit', $event)"
    />
  </AppPage>
</template>

<script setup lang="ts">
import type { NotificationCategory } from '@/components/common/Notification/categories'
import type { NotificationAnswerBridge } from '@/components/common/Notification/renders/NotificationRenderUtils'
import type { WaitingItem } from '@/cx_types'
import type { Notification } from '@/types/apiNotifications'

import { computed, ref } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import AppPage from '@/components/common/AppPage.vue'
import NavLink from '@/components/common/NavLink.vue'
import { t } from '@/i18n'
import { relTime } from '@/lib/relTime'
import { DEFAULT_SHELL, termParams } from '@/lib/shell'
import { taskTitle, topicTitle } from '@/lib/topicState'
import JoinSpaceDialog from '@/views/home/JoinSpaceDialog.vue'
import NotificationFeed from '@/views/home/NotificationFeed.vue'

// 「待办」的画面：上面是**等你处理**，下面是**动态**。
//
// 上面那一半只在这里画：「需要我处理」只在这一处，和任务列表读同一份规则（后端
// `room_task/presentation.py`），范围换成我能看见的全部项目，再按「这件事点的是谁」过滤。
// 它答的是「现在还没处理完的有哪些」，处理完就消失。按项目分组；每一件先说要你做什么，
// 再说是哪件事、等的是什么（提问的原话、改动的主题、停住的原因）。要你拍板的那几件，
// 选项就摆在这一行上；芝士写给你的变更提醒读过就点「标为已读」。
//
// **只认 props**：清单、有没有项目、加载到哪一步、哪一件正在提交、哪个项目在清，
// 动态那一档有哪些条、答邀请那串动作，全是从外面递进来的；重来一次、拍板、答复、标已读、
// 整项目清掉、新建项目、用邀请码加入、换档、翻页、答邀请，都往上发事件。取数、读 store
// 和那些动作在 `views/InboxView.vue` 那只容器里（动态的取数在
// `views/home/useNotificationFeed.ts`，答邀请在 `views/home/useNotificationAnswers.ts`）
// ——于是这一件在测试和 /demo 里都只要一串 props，动态和它的行也只是收 props 的子件。
defineOptions({ name: 'InboxViewView' })

const props = defineProps<{
  /** 一个项目都没有（刚退光 / 删光）：上面给他三条起步路。 */
  noProjects: boolean
  /** 「等你处理」正在读。 */
  loading: boolean
  /** 没读出来：那一块就地换成错误 + 重试。 */
  failed: boolean
  errorReason: string
  items: WaitingItem[]
  /** 上一次动作失败的那句话。 */
  actionError: string
  /** 正在提交的那一件（按 `alertId`）。 */
  acting: number | null
  /** 正在整项目清已读的那个项目。 */
  clearing: string | null
  /** 「动态」现在看的是哪一档。 */
  feedFilter: NotificationCategory
  /** 三档各有多少条；还没问到就是 null。 */
  feedCounts: Record<NotificationCategory, number> | null
  /** 这一档当前这一页。 */
  feedNotifications: Notification[]
  /** 动态正在读这一档。 */
  feedLoading: boolean
  /** 动态整块读失败。 */
  feedFailed: boolean
  /** 只往下翻那一页失败。 */
  feedMoreFailed: boolean
  /** 动态上一次失败的那句话。 */
  feedErrorReason: string
  /** 动态还能往下翻。 */
  feedHasMore: boolean
  /** 动态列表上面那几行之后又来了几条。 */
  feedFresh: number
  /** 问答邀请 / 申请的那串动作与状态；宿主没接就没有那几颗按钮。 */
  answering?: NotificationAnswerBridge
  /** 用邀请码加入空间那个对话框开着没有。 */
  joinOpen: boolean
  /** 正在加入。 */
  joinBusy: boolean
  /** 上一次没加进去的理由。 */
  joinError: string
}>()

const emit = defineEmits<{
  reload: []
  decide: [item: WaitingItem, chosen: string]
  answer: [item: WaitingItem, text: string]
  dismiss: [item: WaitingItem]
  'dismiss-all': [projectId: string]
  'new-project': []
  join: []
  'update:feed-filter': [value: NotificationCategory]
  'feed-mark-all-read': []
  'feed-mark-read': [notificationId: number]
  'feed-delete': [notificationId: number]
  'feed-more': []
  'feed-retry': []
  'feed-show-fresh': []
  'update:join-open': [value: boolean]
  'join-submit': [code: string]
}>()

const projectTerm = termParams(DEFAULT_SHELL)

/** 这一件要你做什么。负责人那一条按停在哪一格说：检查没过，或被退回。 */
function askOf(item: WaitingItem): { text: string; tone: 'warn' | 'danger' } {
  if (item.reason === 'reviewer') return { text: t('home.inbox.reason.reviewer'), tone: 'warn' }
  if (item.reason === 'reporter') return { text: t('home.inbox.reason.reporter'), tone: 'warn' }
  if (item.reason === 'asked') return { text: t('home.inbox.reason.asked'), tone: 'warn' }
  if (item.reason === 'decide') return { text: t('home.inbox.reason.decide'), tone: 'warn' }
  if (item.reason === 'read') return { text: t('home.inbox.reason.read'), tone: 'warn' }
  if (item.phrase === 'checks_failed') return { text: t('home.inbox.reason.checksFailed'), tone: 'danger' }
  return { text: t('home.inbox.reason.bounced'), tone: 'warn' }
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
  for (const item of props.items) {
    const group = groups.find((g) => g.projectId === item.projectId)
    if (group) group.items.push(item)
    else groups.push({ projectId: item.projectId, projectName: item.projectName, items: [item] })
  }
  return groups
})

// 没带选项的决策请求（现在写不进来了，库里还留着以前的）：拍板就是写一句话答复，
// 后端对没有选项的那一条收任何一句。不给这条路，这一行一个按钮都没有，永远清不掉。
const answers = ref<Record<number, string>>({})
function answer(item: WaitingItem) {
  const text = item.alertId == null ? '' : (answers.value[item.alertId] ?? '').trim()
  if (text) emit('answer', item, text)
}

// 一个项目里攒了好几条变更提醒时，一下全部标为已读，不用一条一条点。
const unreadIn = (group: { items: WaitingItem[] }) => group.items.filter((item) => item.reason === 'read').length
</script>

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
.inbox-item__answer {
  flex-wrap: nowrap;
  align-items: center;
}
.inbox-item__answer .v-text-field {
  flex: 1;
  min-width: 0;
}
</style>
