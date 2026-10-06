<script setup lang="ts">
// 题目详情这一层**画的那一半**：题目名那一行的主操作、页签、右栏、问芝士的面板，
// 以及挂在这一层的那几张对话框。取数、读路由、读 store、跳转都在容器 `Detail.vue`
// 里；这里只吃 props、只往上发事件。
import type { Project } from '@/cx_types'
import type { TaskInheritanceData, TaskParticipationIdentity, TaskParticipationInfo } from '@/network/api/tasks/types'
import type { Task, TaskSubmission, TaskSubmissionReview, Team, TeamTaskEligibility } from '@/types'
import type { AssistantConversation, AssistantMessage } from '@/views/tasks/composables/useAssistant'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useDisplay } from 'vuetify'
import dayjs from 'dayjs'

import { taskState as taskStateOf } from '@/utils/tasks'

import TaskEligibilityAlerts from './components/TaskEligibilityAlerts.vue'
import TaskSideView from './components/TaskSideView.vue'

import AssistantPanel from '@/components/assistant/AssistantPanel.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import CheeseAvatar from '@/components/CheeseAvatar.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import TaskSubmissionHistoryView from '@/components/tasks/TaskSubmissionHistoryView.vue'
import { TASK_ROUTE_NAMES } from '@/lib/spaceRouteNames'
import { splitOrigin } from '@/views/spaces/model'
import { TaskDialogs } from '@/views/tasks/components'

const { t } = useI18n()
const { mdAndUp } = useDisplay()
const routeNames = TASK_ROUTE_NAMES

const props = defineProps<{
  loading: boolean
  error: string | null
  taskData: Task | null
  participationInfo: TaskParticipationInfo
  isTaskCreator: boolean
  isSpaceAdmin: boolean
  spaceId: string
  taskId: number
  /** 当前子路由的名字（`route.name`）：页签高亮、右栏要不要画都看它。 */
  activeTabName: string
  /** 此刻停在「交作业」那张表单上（`route.name === submit`）。 */
  onSubmitTab: boolean
  /** 表格与图表两个页签不带右栏。 */
  showSide: boolean
  /** 我最新那一版提交；还没交是 null。 */
  myLatest: { id: number; version: number; review?: TaskSubmissionReview } | null
  // ── 右栏的数据（容器取好递下来） ──
  projects: Project[]
  projectsLoading: boolean
  projectsFailed: boolean
  inheritance: TaskInheritanceData | null
  inheritanceLoading: boolean
  // ── 问芝士 ──
  asking: boolean
  assistantConversations: AssistantConversation[]
  assistantCurrentId: string | null
  assistantTitle: string
  assistantMessages: AssistantMessage[]
  assistantStreaming: string | null
  assistantTool: string | null
  assistantQueued: boolean
  assistantNotice: string | null
  assistantCreditRefused: boolean
  assistantBusy: boolean
  // ── 逐版评审对话框 ──
  reviewing: { id: number; name: string } | null
  reviewSubmissions: TaskSubmission[]
  reviewHasMore: boolean
  reviewLoadingMore: boolean
  reviewRefreshing: boolean
  reviewTotal: number
  reviewSubmitting: boolean
  // ── 领取/退队/实名那几张对话框 ──
  availableTeams: TeamTaskEligibility[]
  loadingTeams: boolean
  joinedTeams: Team[]
  selectedLeaveTeamId: number | null
}>()

const emit = defineEmits<{
  retry: []
  edit: []
  delete: []
  claim: []
  'open-assistant': []
  'update:asking': [value: boolean]
  send: [text: string]
  stop: []
  'new-conversation': []
  'select-conversation': [id: string]
  leave: []
  'new-project': []
  'reload-projects': []
  'close-review': []
  'load-more-review': []
  'submit-review': [values: { accepted?: boolean; score?: number; comment?: string }]
  'cancel-review': []
}>()

const canManage = computed(() => props.isTaskCreator || props.isSpaceAdmin)

// ── 题目本身 ────────────────────────────────────────────────────────────────────

/** 简介里那一段「出处」（`〔…〕`）是老库把来源写进 `intro` 的约定，`splitOrigin` 把
 *  它摘出来。摘要与出处是同一段文本的两半，所以两个都由它给，不各填各的。 */
const intro = computed(() => splitOrigin(props.taskData?.intro ?? ''))
const publisherName = computed(() => props.taskData?.creator.nickname || props.taskData?.creator.username || '')
const publishedOn = computed(() => {
  const at = props.taskData?.createdAt
  return at ? dayjs(at).format(t('tasks.page.dateFormat')) : ''
})

// ── 领取 ───────────────────────────────────────────────────────────────────────
//
// 判据全部是接口给的事实：这道题的审核状态、我的报名记录的审核状态、截止/开始时刻、
// 领取人数与上限。

const identities = computed(() => props.participationInfo.identities ?? [])
/** 我那一份报名：批过的优先，其次是还在等批的。后端暂时还允许一人多份，这里只取一份。 */
const myIdentity = computed<TaskParticipationIdentity | null>(
  () =>
    identities.value.find((i) => i.approved === 'APPROVED') ??
    identities.value.find((i) => i.approved === 'NONE') ??
    null
)
const joined = computed(
  () => props.taskData?.joined === true || identities.value.some((i) => i.approved === 'APPROVED')
)
const myClaimPending = computed(() => !joined.value && identities.value.some((i) => i.approved === 'NONE'))
const myClaimRejected = computed(
  () => identities.value.length > 0 && identities.value.every((i) => i.approved === 'DISAPPROVED')
)

/** `participantLimit` 的 0 是「不限」，不是「一个人都不许」。 */
const limit = computed(() => ((props.taskData?.participantLimit ?? 0) > 0 ? props.taskData!.participantLimit : null))
const claimCount = computed(() => props.taskData?.participants.total ?? 0)
const deadlinePassed = computed(() => {
  const at = props.taskData?.deadline
  return at != null && at < Date.now()
})
const notStarted = computed(() => {
  const at = props.taskData?.registrationStartAt
  return at != null && at > Date.now()
})
const full = computed(() => limit.value !== null && claimCount.value >= limit.value)

/** 题目名旁边那一枚：这道题此刻对所有人是什么状态（和我领没领无关）。 */
const taskState = computed(() => {
  if (!props.taskData) return null
  const state = taskStateOf(props.taskData)
  return { label: t(`tasks.page.state.${state.key}`), tone: state.tone }
})

/** 领取那颗按钮。领不了的时候按钮还在，灰着，字写清为什么。 */
const claim = computed(() => {
  const task = props.taskData
  if (!task) return null
  if (myClaimPending.value) return { label: t('tasks.page.claim.pending'), disabled: true }
  if (myClaimRejected.value) return { label: t('tasks.page.claim.rejected'), disabled: true }
  if (task.approved !== 'APPROVED') return { label: t('tasks.page.claim.notApproved'), disabled: true }
  if (deadlinePassed.value) return { label: t('tasks.page.state.closed'), disabled: true }
  if (notStarted.value) return { label: t('tasks.page.state.notStarted'), disabled: true }
  if (full.value) return { label: t('tasks.page.state.full'), disabled: true }
  return { label: t('tasks.page.claim.claim'), disabled: false }
})

/** 这一颗按钮不自己发请求：发 `claim`，由容器的 `TaskDialogs` 与 `useTaskParticipation`
 *  接着走（实名确认、团队选择都在里面）。 */
function onClaim() {
  if (claim.value?.disabled) return
  emit('claim')
}

// ── 我自己的那一份进度 ──────────────────────────────────────────────────────────

/** 领了之后的主操作：交第一版或再交一版。服务端不收的时候不给这颗按钮：题目已结束，
 *  或者只能交一次而且已经交过。 */
const submitAction = computed(() => {
  if (!joined.value || !props.taskData) return null
  // 已经在交作业的表单上：表单自己有「提交」，这一行不再给第二颗。
  if (props.onSubmitTab) return null
  if (props.taskData.endedAt) return null
  if (props.myLatest && !props.taskData.resubmittable) return null
  return props.myLatest ? t('tasks.page.submit.again') : t('tasks.page.submit.first')
})

// ── 页签 ───────────────────────────────────────────────────────────────────────

const params = computed(() => ({ spaceId: props.spaceId, taskId: props.taskId }))
const listTo = computed(() => ({ name: 'SpacesDetailTasksList', params: { spaceId: props.spaceId } }))

const tabs = computed(() => {
  const list: {
    key: string
    label: string
    to: { name: string; params: Record<string, string | number> }
    count?: number
    also?: string[]
  }[] = [{ key: 'brief', label: t('tasks.page.tabs.brief'), to: { name: routeNames.detail, params: params.value } }]
  if (joined.value) {
    list.push({
      key: 'mine',
      label: t('tasks.page.tabs.mine'),
      to: { name: routeNames.submissions, params: params.value },
      count: props.myLatest?.version,
      also: [routeNames.submit],
    })
  }
  if (canManage.value) {
    list.push({
      key: 'roster',
      label: t('tasks.page.tabs.roster'),
      to: { name: routeNames.participants, params: params.value },
      count: claimCount.value,
    })
    list.push({
      key: 'data',
      label: t('tasks.page.tabs.data'),
      to: { name: routeNames.insights, params: params.value },
    })
  }
  return list
})

function isActive(tab: { to: { name: string }; also?: string[] }): boolean {
  return props.activeTabName === tab.to.name || (tab.also?.includes(props.activeTabName) ?? false)
}
</script>

<template>
  <PageHeader>
    <nav class="td__crumb">
      <router-link :to="listTo" class="td__crumb-parent">{{ t('spaces.detail.allContests') }}</router-link>
      <v-icon size="16" class="td__crumb-sep">mdi-chevron-right</v-icon>
      <span class="td__crumb-here" data-user-content>{{ taskData?.name ?? '' }}</span>
    </nav>
    <template #actions>
      <template v-if="canManage">
        <BaseButton kind="secondary" prepend-icon="mdi-pencil-outline" @click="emit('edit')">{{
          t('tasks.page.edit')
        }}</BaseButton>
        <BaseButton kind="ghost" prepend-icon="mdi-delete-outline" @click="emit('delete')">{{
          t('tasks.page.delete')
        }}</BaseButton>
      </template>
    </template>
  </PageHeader>

  <div v-if="loading" class="py-12 text-center">
    <v-progress-circular indeterminate color="primary" />
  </div>

  <!-- A failed read trades this block for the error (docs/design-system.md §3.10) instead of degrading to an empty state. -->
  <BaseLoadError v-else-if="error" :title="t('tasks.loadError.title')" :error="error" @retry="emit('retry')" />

  <div v-else-if="taskData" class="td">
    <header class="td__head">
      <div class="td__lead">
        <div class="td__titleline">
          <h1 class="td__title t-page-title" data-user-content>{{ taskData.name }}</h1>
          <span v-if="taskState" class="td__state" :class="`td__state--${taskState.tone}`">
            {{ taskState.label }}
          </span>
        </div>
        <p class="td__by t-meta-read">
          <span>{{ t('tasks.page.publishedBy', { name: publisherName, date: publishedOn }) }}</span>
          <span v-if="taskData.category" data-user-content>{{ taskData.category.name }}</span>
          <span v-if="intro.origin" class="td__origin">{{ intro.origin }}</span>
          <span v-for="topic in taskData.topics ?? []" :key="topic.id" class="td__topic" data-user-content
            >#{{ topic.name }}</span
          >
        </p>
        <p v-if="intro.summary" class="td__summary" data-user-content>{{ intro.summary }}</p>
      </div>

      <div class="td__act">
        <BaseButton
          kind="secondary"
          class="td__ask"
          :active="asking"
          data-testid="task-ask"
          @click="emit('open-assistant')"
        >
          <span class="td__ask-mark" aria-hidden="true"><CheeseAvatar :size="18" /></span>
          {{ t('tasks.assistant.ask') }}
        </BaseButton>
        <BaseButton
          v-if="submitAction"
          kind="primary"
          prepend-icon="mdi-plus"
          :to="{ name: routeNames.submit, params }"
        >
          {{ submitAction }}
        </BaseButton>
        <BaseButton
          v-else-if="!joined && claim"
          class="td__claim"
          data-testid="task-claim"
          :kind="claim.disabled ? 'ghost' : canManage ? 'secondary' : 'primary'"
          :disabled="claim.disabled"
          @click="onClaim"
        >
          {{ claim.label }}
        </BaseButton>
      </div>
    </header>

    <!-- 驳回原因：接口给的是审核那一刻写下的那一段，没有就不显示，不补一句套话。 -->
    <v-alert
      v-if="taskData.approved === 'DISAPPROVED' && taskData.rejectReason"
      type="error"
      variant="tonal"
      density="comfortable"
      class="td__reject"
    >
      {{ t('tasks.page.rejectReason', { reason: taskData.rejectReason }) }}
    </v-alert>
    <TaskEligibilityAlerts :task="taskData" class="td__eligibility" />

    <nav class="td__tabs" :aria-label="t('tasks.page.tabs.label')">
      <router-link
        v-for="tab in tabs"
        :key="tab.key"
        :to="tab.to"
        class="td__tab"
        :class="{ 'td__tab--on': isActive(tab) }"
        :aria-current="isActive(tab) ? 'page' : undefined"
      >
        {{ tab.label }}<span v-if="tab.count" class="td__tab-count t-num">{{ tab.count }}</span>
      </router-link>
    </nav>

    <div class="td__body" :class="{ 'td__body--split': showSide && !(asking && mdAndUp) }">
      <div class="td__main">
        <router-view v-slot="{ Component }">
          <component
            :is="Component"
            v-if="Component"
            :task-data="taskData"
            :participation-info="participationInfo"
            :is-creator="isTaskCreator"
            :is-admin="isSpaceAdmin"
          />
        </router-view>
      </div>
      <TaskSideView
        v-if="showSide && !(asking && mdAndUp)"
        class="td__side"
        :task="taskData"
        :identity="myIdentity"
        :latest="myLatest"
        :projects="projects"
        :projects-loading="projectsLoading"
        :projects-failed="projectsFailed"
        :inheritance="inheritance"
        :inheritance-loading="inheritanceLoading"
        @leave="emit('leave')"
        @new-project="emit('new-project')"
        @reload-projects="emit('reload-projects')"
      />
    </div>
  </div>

  <!-- 只在打开时才挂：停靠的抽屉要向页面外框登记自己，关着的时候不占那个位置。 -->
  <v-navigation-drawer
    v-if="mdAndUp && asking"
    :model-value="asking"
    location="right"
    width="380"
    class="td-ask"
    @update:model-value="(open: boolean) => emit('update:asking', open)"
  >
    <AssistantPanel
      :conversations="assistantConversations"
      :current-id="assistantCurrentId"
      :title="assistantTitle"
      :messages="assistantMessages"
      :streaming="assistantStreaming"
      :tool="assistantTool"
      :queued="assistantQueued"
      :notice="assistantNotice"
      :credit-refused="assistantCreditRefused"
      :busy="assistantBusy"
      @send="(text: string) => emit('send', text)"
      @stop="emit('stop')"
      @new="emit('new-conversation')"
      @select="(id: string) => emit('select-conversation', id)"
      @close="emit('update:asking', false)"
    />
  </v-navigation-drawer>
  <v-bottom-sheet
    v-else-if="!mdAndUp"
    :model-value="asking"
    class="td-ask-sheet"
    @update:model-value="(open: boolean) => emit('update:asking', open)"
  >
    <div class="td-ask-sheet__body">
      <AssistantPanel
        :conversations="assistantConversations"
        :current-id="assistantCurrentId"
        :title="assistantTitle"
        :messages="assistantMessages"
        :streaming="assistantStreaming"
        :tool="assistantTool"
        :queued="assistantQueued"
        :notice="assistantNotice"
        :busy="assistantBusy"
        @send="(text: string) => emit('send', text)"
        @stop="emit('stop')"
        @new="emit('new-conversation')"
        @select="(id: string) => emit('select-conversation', id)"
        @close="emit('update:asking', false)"
      />
    </div>
  </v-bottom-sheet>

  <v-dialog :model-value="reviewing !== null" max-width="860" scrollable @update:model-value="emit('close-review')">
    <TaskSubmissionHistoryView
      v-if="reviewing && taskData"
      :submissions="reviewSubmissions"
      :has-more="reviewHasMore"
      :loading-more="reviewLoadingMore"
      :refreshing="reviewRefreshing"
      :total="reviewTotal"
      :submitting="reviewSubmitting"
      :reviewable="true"
      :is-dialog="true"
      :outlined="true"
      :highlight-latest="true"
      :title="t('tasks.page.reviewTitle', { name: reviewing.name })"
      @load-more="emit('load-more-review')"
      @submit-review="
        (values: { accepted?: boolean; score?: number; comment?: string }) => emit('submit-review', values)
      "
      @cancel-review="emit('cancel-review')"
    />
  </v-dialog>

  <!-- 领取/退队/实名那几张对话框：老机器，事件总线上接了它。 -->
  <TaskDialogs
    :task-data="taskData"
    :available-teams="availableTeams"
    :loading-teams="loadingTeams"
    :joined-teams="joinedTeams"
    :selected-leave-team-id="selectedLeaveTeamId"
    :participation-info="participationInfo"
  />
</template>

<style scoped lang="scss">
@use '../../styles/breakpoints.scss' as bp;

.td__crumb {
  display: flex;
  gap: 6px;
  align-items: center;
  min-width: 0;
  font-size: 14px;
}

.td__crumb-parent {
  color: var(--muted);
  text-decoration: none;
  white-space: nowrap;
}

.td__crumb-parent:hover {
  color: var(--ink);
}

.td__crumb-sep {
  color: var(--faint);
}

.td__crumb-here {
  overflow: hidden;
  color: var(--ink);
  font-weight: 600;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.td {
  padding: 24px 32px 40px;
}

@media (max-width: 600px) {
  .td {
    padding: 16px 16px 32px;
  }
}

.td__head {
  display: flex;
  gap: 24px;
  align-items: flex-start;
  margin-bottom: 20px;
}

.td__lead {
  flex: 1;
  min-width: 0;
}

.td__titleline {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 10px;
  align-items: center;
  margin-bottom: 4px;
}

.td__title {
  margin: 0;
  overflow-wrap: anywhere;
}

.td__state {
  padding: 1px 10px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-pill);
  font-size: 12px;
  line-height: var(--lh-12);
  white-space: nowrap;
}

.td__state--ok {
  color: var(--ok-ink);
}

.td__state--muted {
  color: var(--muted);
}

.td__state--danger {
  color: var(--danger-ink);
}

.td__by {
  display: flex;
  flex-wrap: wrap;
  gap: 2px 0;
  margin: 0 0 10px;
}

.td__by > span + span::before {
  margin: 0 8px;
  color: var(--faint);
  content: '·';
}

.td__summary {
  margin: 0;
  color: var(--muted);
  font-size: 15px;
  line-height: var(--lh-15-reading);
  white-space: pre-wrap;
}

.td__act {
  display: flex;
  flex: none;
  gap: 8px;
  padding-top: 2px;
}

.td__ask-mark {
  display: inline-flex;
  margin-right: 6px;
}

.td-ask :deep(.v-navigation-drawer__content) {
  overflow: hidden;
}

.td-ask-sheet__body {
  height: 85dvh;
  overflow: hidden;
  background: var(--surface);
  border-top-left-radius: var(--radius-lg);
  border-top-right-radius: var(--radius-lg);
}

@media (max-width: 600px) {
  .td__head {
    flex-direction: column;
    gap: 12px;
  }

  .td__act {
    width: 100%;
  }

  .td__act .v-btn {
    flex: 1;
  }
}

.td__reject,
.td__eligibility {
  margin-bottom: 16px;
}

.td__tabs {
  display: flex;
  gap: 22px;
  overflow-x: auto;
  border-bottom: 1px solid var(--line);
  scrollbar-width: none;
}

.td__tab {
  flex: none;
  margin-bottom: -1px;
  padding: 10px 0;
  border-bottom: 2px solid transparent;
  color: var(--muted);
  font-size: 14px;
  text-decoration: none;
  white-space: nowrap;
}

.td__tab:hover {
  color: var(--ink);
}

.td__tab--on {
  border-bottom-color: var(--ink);
  color: var(--ink);
  font-weight: 600;
}

.td__tab-count {
  margin-left: 4px;
  color: var(--faint);
  font-size: 12px;
}

.td__body {
  padding-top: 20px;
}

.td__body--split {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 260px;
  gap: 40px;
  align-items: start;
}

// 断点收进共享 token（`styles/breakpoints.scss`）：1000 → 960（`$bp-mobile`）。
@include bp.below(bp.$bp-mobile) {
  .td__body--split {
    grid-template-columns: minmax(0, 1fr);
    gap: 32px;
  }

  // 窄屏上右栏（我的进度、题目信息）排在页签内容前面：进度比正文更常看。
  .td__side {
    order: -1;
  }
}

.td__main {
  min-width: 0;
}
</style>
