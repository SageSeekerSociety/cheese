<script setup lang="ts">
// 题目详情：上面一块是题目名和这一页的主操作，下面是页签。页签内容是子路由
// （说明 / 我的提交 / 领取者 / 数据），地址各自不变，点了在原地换内容。
// 内容类页签右边带一栏：我的进度（领了才有）和题目信息；领取者与数据是表格和图表，不带。
//
// 领取这条路（实名确认、团队选择、退出）走的是 `useTaskParticipation` 与 `TaskDialogs`：
// 这一页的「领取」按钮只往 `useEvents()` 总线上发 `join-clicked`，不自己发请求。
import type { TaskSubmissionReview } from '@/types'

import { computed, defineAsyncComponent, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'
import dayjs from 'dayjs'

import { canStillAct, taskState as stateOfTask } from '@/utils/tasks'

import { usePageTitle } from '@/composables/usePageTitle'

import TaskEligibilityAlerts from './components/TaskEligibilityAlerts.vue'
import TaskSide from './components/TaskSide.vue'

import { TaskBoardApi } from '@/api/taskBoard'
import BaseButton from '@/components/base/BaseButton.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import CheeseAvatar from '@/components/CheeseAvatar.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import TaskStageTimeline from '@/components/tasks/TaskStageTimeline.vue'
import { TASK_ROUTE_NAMES } from '@/lib/spaceRouteNames'
import { agentDocked, agentOpen, toggleAgent, useAgentContext, useAgentSurface } from '@/stores/agent'
import { splitOrigin } from '@/views/spaces/model'
import { TaskDialogs } from '@/views/tasks/components'
import {
  useTaskData,
  useTaskManagement,
  useTaskParticipation,
  useTaskProgress,
  useTeamParticipation,
} from '@/views/tasks/composables'
import { useEvents } from '@/views/tasks/events'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()

const routeNames = TASK_ROUTE_NAMES
const events = useEvents()
const { setDynamicTitle } = usePageTitle()

const spaceId = computed(() => String(route.params.spaceId))
const listTo = computed(() => ({ name: 'SpacesDetailTasksList', params: { spaceId: spaceId.value } }))

const taskDataModule = useTaskData()
const { taskId, taskData, loading, error, isTaskCreator, isSpaceAdmin, participationInfo, loadTaskData } =
  taskDataModule

const { onJoinTaskClicked, openApprovedClaim, confirmLeaveTask, handleVerifyInfoSubmit, leaveTaskWithTeam } =
  useTaskParticipation(taskDataModule)
const {
  availableTeams,
  loadingTeams,
  joinedTeams,
  selectedLeaveTeamId,
  selectTeam,
  selectLeaveTeam,
  confirmLeaveSelectedTeam,
  loadJoinedTeams,
} = useTeamParticipation(taskDataModule)
const { confirmDeleteTask } = useTaskManagement(taskDataModule)
// 每个阶段上的进度：说明、我的提交、数据几个页签和右栏都读这一份。
const { progress, load: loadProgress } = useTaskProgress()

const canManage = computed(() => isTaskCreator.value || isSpaceAdmin.value)

// ── 问芝士 ─────────────────────────────────────────────────────────────────────
//
// 个人芝士的面板是全站那一个（layouts/AgentHost.vue）；这一页只说它是哪道题，芝士读到的
// 「当前页面」就是它。芝士在这里改了这道题并保存了，页面上的题目跟着换。

const { mdAndUp } = useDisplay()
useAgentContext(() => ({
  page: { kind: 'task', id: taskId.value },
  label: taskData.value?.name ? t('tasks.assistant.page.problem', { name: taskData.value.name }) : undefined,
  intro: t('tasks.assistant.reads'),
  greeting: t('tasks.assistant.greeting'),
  starters: (['basics', 'start', 'hard'] as const).map((k) => t(`tasks.assistant.starters.${k}`)),
  placeholder: t('tasks.assistant.placeholder'),
  hint: t('tasks.assistant.hint'),
}))
useAgentSurface(() => ({
  on: {
    published: (done) => {
      if (done.task.id === taskId.value) void loadTaskData({ quiet: true })
    },
  },
}))

// ── 题目本身 ────────────────────────────────────────────────────────────────────

/** 简介里那一段「出处」（`〔…〕`）是老库把来源写进 `intro` 的约定，`splitOrigin` 把
 *  它摘出来。摘要与出处是同一段文本的两半，所以两个都由它给，不各填各的。 */
const intro = computed(() => splitOrigin(taskData.value?.intro ?? ''))
const publisherName = computed(() => taskData.value?.creator.nickname || taskData.value?.creator.username || '')
const publishedOn = computed(() => {
  const at = taskData.value?.createdAt
  return at ? dayjs(at).format(t('tasks.page.dateFormat')) : ''
})

// ── 领取 ───────────────────────────────────────────────────────────────────────
//
// 判据全部是接口给的事实：这道题的审核状态、我的报名记录的审核状态、截止/开始时刻、
// 领取人数与上限。

const identities = computed(() => participationInfo.value.identities ?? [])
/** 我那一份报名：批过的优先，其次是还在等批的。后端暂时还允许一人多份，这里只取一份。 */
const myIdentity = computed(
  () =>
    identities.value.find((i) => i.approved === 'APPROVED') ??
    identities.value.find((i) => i.approved === 'NONE') ??
    null
)
/** 领到了：报名批过。接口的 `joined` 不是这个意思 —— 它对还在等批的报名也是 true（只排除被拒的），
 *  拿它当「领到了」，等批的人就会看到一颗服务端不收的「提交作业」。批没批看 `submittable`
 *  （服务端按批过的报名算）和我那份报名自己的审核状态。 */
const joined = computed(
  () => taskData.value?.submittable === true || identities.value.some((i) => i.approved === 'APPROVED')
)
/** 报了还没批：接口说我在这道题里（`joined`），或者我有一份等批的报名，但还没批过。 */
const myClaimPending = computed(
  () => !joined.value && (taskData.value?.joined === true || identities.value.some((i) => i.approved === 'NONE'))
)
const myClaimRejected = computed(
  () => identities.value.length > 0 && identities.value.every((i) => i.approved === 'DISAPPROVED')
)

/** `participantLimit` 的 0 是「不限」，不是「一个人都不许」。 */
const limit = computed(() => ((taskData.value?.participantLimit ?? 0) > 0 ? taskData.value!.participantLimit : null))
/** 参与者：申请通过的人。「参与者」页签上的数和名额满没满都按它；还在等批准的在页签里另成「申请中」一格。 */
const participantCount = computed(() => taskData.value?.participants.approved ?? 0)
const deadlinePassed = computed(() => {
  const at = taskData.value?.deadline
  return at != null && at < Date.now()
})
const notStarted = computed(() => {
  const at = taskData.value?.registrationStartAt
  return at != null && at > Date.now()
})
const full = computed(() => limit.value !== null && participantCount.value >= limit.value)

/** 直接开始的题点了就开始；申请制的题先申请，出题人通过才开始。 */
const startsDirectly = computed(() => taskData.value?.startMode === 'DIRECT')
/** 我（或我的团队）从这道题来的项目：有它，主操作就是「继续做」，打开它。申请还在等的
 *  不给（接口只对通过了的领取给它）：早先的版本申请时就开了项目，等通过前只显示在等。 */
const myProject = computed(() => taskData.value?.myProject ?? null)
const myProjectTo = computed(() => (myProject.value ? `/projects/${myProject.value.id}` : ''))
/** 通过了还没有项目（申请制的题刚通过，或上次建仓库没成）：主操作是「开始做这道题」，
 *  开始做就是用这道题新建项目。 */
const approvedWithoutProject = computed(() => joined.value && !myProject.value)

/** 题目名旁边那一枚：题目自己的状态，对谁都一样（Zhifei 2026-10-26）。我这一份走到哪了在右栏
 *  「我的进度」，我现在能做什么在按钮上。 */
const taskState = computed(() => {
  if (!taskData.value) return null
  const state = stateOfTask(taskData.value)
  return { label: t(`tasks.page.state.${state.key}`), tone: state.tone }
})

/** 主操作那颗按钮：我现在能做什么，按我自己这一份说。做不了的时候按钮还在，灰着，字写清为什么：
 *  - 手上有通过了的领取、还没开始：我自己的提交截止没到就能开始（两种开始方式都一样，`canStillAct`），
 *    过了是「已过期」；题目结束了是「已结束」。题目的截止不挡我。
 *  - 申请在等、被拒了：写这一份走到哪了。
 *  - 没有领取：题目的截止、名额、开放时间挡着的，写题目的状态（申请制「申请已截止」，直接开始「已截止」）。 */
const claim = computed(() => {
  const task = taskData.value
  if (!task) return null
  if (approvedWithoutProject.value) {
    if (canStillAct(task)) return { label: t('tasks.page.claim.start'), disabled: false }
    return {
      label: task.endedAt != null ? t('tasks.page.state.ended') : t('tasks.page.claim.expired'),
      disabled: true,
    }
  }
  if (myClaimPending.value) return { label: t('tasks.page.mine.pending'), disabled: true }
  if (myClaimRejected.value) return { label: t('tasks.page.mine.rejected'), disabled: true }
  if (task.approved !== 'APPROVED')
    return {
      label: startsDirectly.value ? t('tasks.page.claim.notApprovedStart') : t('tasks.page.claim.notApproved'),
      disabled: true,
    }
  if (task.endedAt != null) return { label: t('tasks.page.state.ended'), disabled: true }
  if (deadlinePassed.value)
    return {
      label: startsDirectly.value ? t('tasks.page.state.closed') : t('tasks.page.state.applyClosed'),
      disabled: true,
    }
  if (notStarted.value) return { label: t('tasks.page.state.notStarted'), disabled: true }
  if (full.value) return { label: t('tasks.page.state.full'), disabled: true }
  return { label: startsDirectly.value ? t('tasks.page.claim.start') : t('tasks.page.claim.claim'), disabled: false }
})

/** 这一颗按钮不自己发请求：发 `join-clicked`，由 `TaskDialogs` 与 `useTaskParticipation` 接着走
 *  （实名确认、团队选择都在里面）。通过了还没开始的，打开开始的确认框。 */
function onClaim() {
  if (claim.value?.disabled) return
  const approved = myIdentity.value
  if (approvedWithoutProject.value && approved?.approved === 'APPROVED') {
    openApprovedClaim({ type: approved.type, memberId: approved.memberId })
    return
  }
  events.emit('join-clicked')
}

// ── 我自己的那一份进度 ──────────────────────────────────────────────────────────

/** 我最新那一版提交（含判没判）。取不到就当还没交 —— 不编一个版本出来。 */
const myLatest = ref<{ id: number; version: number; review?: TaskSubmissionReview } | null>(null)

async function loadMine() {
  const participantId = myIdentity.value?.id
  if (participantId === undefined || !taskData.value) return
  try {
    const data = await TaskBoardApi.listSubmissions(taskData.value.id, participantId, {
      pageSize: 1,
      sort_by: 'createdAt',
      sort_order: 'desc',
      queryReview: true,
    })
    const latest = data.submissions?.[0]
    myLatest.value = latest ? { id: latest.id, version: latest.version, review: latest.review } : null
  } catch {
    myLatest.value = null
  }
}

/** 领了之后的主操作：交第一版或再交一版。服务端不收的时候不给这颗按钮：这一份再也动不了，
 *  或者只能交一次而且已经交过。 */
const submitAction = computed(() => {
  if (!joined.value || !taskData.value) return null
  // 已经在交作业的表单上：表单自己有「提交」，这一行不再给第二颗。
  if (route.name === routeNames.submit) return null
  // 交不了的不给：过了我自己的截止、题目结束了，服务端都不收（`canStillAct`）。
  if (!canStillAct(taskData.value)) return null
  if (myLatest.value && !taskData.value.resubmittable) return null
  return myLatest.value ? t('tasks.page.submit.again') : t('tasks.page.submit.first')
})

// ── 页签 ───────────────────────────────────────────────────────────────────────

const params = computed(() => ({ spaceId: spaceId.value, taskId: taskId.value }))

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
      count: myLatest.value?.version,
      also: [routeNames.submit],
    })
  }
  if (canManage.value) {
    list.push({
      key: 'roster',
      label: t('tasks.page.tabs.roster'),
      to: { name: routeNames.participants, params: params.value },
      count: participantCount.value,
    })
    list.push({
      key: 'data',
      label: t('tasks.page.tabs.data'),
      to: { name: routeNames.insights, params: params.value },
    })
  }
  list.push({
    key: 'versions',
    label: t('tasks.page.tabs.versions'),
    to: { name: routeNames.versions, params: params.value },
  })
  return list
})

function isActive(tab: { to: { name: string }; also?: string[] }): boolean {
  return route.name === tab.to.name || (tab.also?.includes(String(route.name)) ?? false)
}

/** 表格和图表那两个页签不带右栏：它们要整宽。 */
const showSide = computed(() => route.name !== routeNames.participants && route.name !== routeNames.insights)

// ── 逐版评审 ────────────────────────────────────────────────────────────────────
//
// 「领取者」页签点「评审」或「查看提交」，在总线上说要看谁的；对话框挂在这一层，因为
// 它自己取数。判了一版就发 `participation-changed`：领取者的表和我自己的进度都跟着重读。

const TaskSubmissionHistory = defineAsyncComponent(() => import('@/components/tasks/TaskSubmissionHistory.vue'))
const reviewing = ref<{ id: number; name: string } | null>(null)
/** 分了阶段的题，每个阶段单独批改：对话框里先选批哪个阶段。 */
const reviewStage = ref(0)
const stages = computed(() => taskData.value?.stages ?? [])

function closeReview() {
  reviewing.value = null
}

function editTask() {
  router.push({ name: routeNames.edit, params: params.value })
}

// ── 装载 ───────────────────────────────────────────────────────────────────────

async function load() {
  await loadTaskData()
  const task = taskData.value
  if (!task) return
  setDynamicTitle(task.name, 'TaskShell')
  if (task.submitterType === 'TEAM') loadJoinedTeams()
  if (myIdentity.value) loadMine()
  loadProgress(task.id)
}

/** 有人的领取或提交变了：页头的主操作和右栏的进度都从我那一份报名和它最新一版来，两样一起重读。
 *  不转圈：页签内容（比如刚批完的领取者表）留在原地。 */
async function reloadMine() {
  await loadTaskData({ quiet: true })
  if (taskData.value) loadProgress(taskData.value.id)
  if (myIdentity.value) await loadMine()
  else myLatest.value = null
}

onMounted(() => {
  // 领取这条路上的事件由 `useTaskParticipation` / `useTeamParticipation` 处理，这里把总线接上；
  events.on('join-clicked', onJoinTaskClicked)
  events.on('leave-clicked', confirmLeaveTask)
  events.on('submit-verify', (data) => {
    handleVerifyInfoSubmit(data).catch((err: Error) => console.error('提交验证信息失败', err))
  })
  events.on('select-team', (teamId) => selectTeam(teamId))
  events.on('select-leave-team', (teamId) => selectLeaveTeam(teamId))
  events.on('confirm-leave-team', () => {
    confirmLeaveSelectedTeam().catch((err: Error) => console.error('离开队伍失败', err))
  })
  events.on('leave-team', (teamId) => leaveTaskWithTeam(teamId))
  events.on('review-participant', (who) => {
    reviewStage.value = 0
    reviewing.value = who
  })
  // 批了领取、判了一版、交上一版：页头、右栏的进度和页签上的版本号都跟着重读。
  events.on('participation-changed', () => {
    reloadMine()
  })

  load()
})
</script>

<template>
  <PageHeader>
    <nav class="td__crumb">
      <router-link :to="listTo" class="td__crumb-parent">{{ t('spaces.detail.allContests') }}</router-link>
      <v-icon size="16" class="td__crumb-sep">mdi-chevron-right</v-icon>
      <span class="td__crumb-here" data-user-content>{{ taskData?.name ?? '' }}</span>
    </nav>
    <template #actions>
      <BaseButton
        kind="ghost"
        class="td__ask"
        :active="agentOpen"
        data-testid="task-ask"
        :aria-label="t('tasks.assistant.ask')"
        :title="t('tasks.assistant.ask')"
        @click="toggleAgent"
      >
        <CheeseAvatar :size="20" />
      </BaseButton>
      <template v-if="canManage">
        <BaseButton kind="secondary" prepend-icon="mdi-pencil-outline" @click="editTask">{{
          t('tasks.page.edit')
        }}</BaseButton>
        <BaseButton kind="ghost" prepend-icon="mdi-delete-outline" @click="confirmDeleteTask">{{
          t('tasks.page.delete')
        }}</BaseButton>
      </template>
    </template>
  </PageHeader>

  <div v-if="loading" class="py-12 text-center">
    <v-progress-circular indeterminate color="primary" />
  </div>

  <!-- A failed read trades this block for the error (docs/design-system.md §3.10) instead of degrading to an empty state. -->
  <BaseLoadError v-else-if="error" :title="t('tasks.loadError.title')" :error="error" @retry="load" />

  <div v-else-if="taskData" class="td">
    <header class="td__head">
      <div class="td__lead">
        <div class="td__titleline">
          <h1 class="td__title t-page-title" data-user-content>{{ taskData.name }}</h1>
          <span v-if="taskState" class="td__state" :class="`td__state--${taskState.tone}`" data-testid="task-state">
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

      <!-- One main action: start / apply / continue. Handing in is a quieter text button beside it. -->
      <div v-if="myProject || claim || submitAction" class="td__act">
        <BaseButton
          v-if="submitAction"
          kind="ghost"
          prepend-icon="mdi-plus"
          data-testid="task-submit"
          :to="{ name: routeNames.submit, params }"
        >
          {{ submitAction }}
        </BaseButton>
        <BaseButton v-if="myProject" kind="primary" data-testid="task-continue" :to="myProjectTo">
          {{ t('tasks.page.claim.continue') }}
        </BaseButton>
        <BaseButton
          v-else-if="claim"
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

    <div class="td__body" :class="{ 'td__body--split': showSide && !(agentOpen && agentDocked && mdAndUp) }">
      <div class="td__main">
        <router-view v-slot="{ Component }">
          <component
            :is="Component"
            v-if="Component"
            :task-data="taskData"
            :participation-info="participationInfo"
            :is-creator="isTaskCreator"
            :is-admin="isSpaceAdmin"
            :progress="progress"
          />
        </router-view>
      </div>
      <TaskSide
        v-if="showSide && !(agentOpen && agentDocked && mdAndUp)"
        class="td__side"
        :task="taskData"
        :identity="myIdentity"
        :latest="myLatest"
        :progress="progress"
        @leave="events.emit('leave-clicked')"
      />
    </div>
  </div>

  <v-dialog :model-value="reviewing !== null" max-width="860" scrollable @update:model-value="closeReview">
    <div v-if="reviewing && taskData" class="td__review">
      <TaskStageTimeline
        v-if="stages.length"
        class="td__review-stages"
        :stages="stages"
        selectable
        :current="reviewStage"
        @select="reviewStage = $event"
      />
      <TaskSubmissionHistory
        :task-id="taskData.id"
        :participant-id="reviewing.id"
        :stage="stages[reviewStage]?.id ?? null"
        :reviewable="true"
        :is-dialog="true"
        :outlined="true"
        :highlight-latest="true"
        :title="t('tasks.page.reviewTitle', { name: reviewing.name })"
        @changed="events.emit('participation-changed')"
      />
    </div>
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

.td__review {
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow-y: auto;
  border-radius: var(--radius-lg);
  background: var(--surface);
}

.td__review-stages {
  padding: 16px 16px 0;
}

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

.td__ask {
  min-width: 36px;
  padding: 0 8px;
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
