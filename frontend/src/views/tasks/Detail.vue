<template>
  <DetailView
    :loading="loading"
    :error="error"
    :task-data="taskData"
    :participation-info="participationInfo"
    :is-task-creator="isTaskCreator"
    :is-space-admin="isSpaceAdmin"
    :space-id="spaceId"
    :task-id="taskId"
    :active-tab-name="activeTabName"
    :on-submit-tab="onSubmitTab"
    :show-side="showSide"
    :my-latest="myLatest"
    :projects="projects"
    :projects-loading="projectsLoading"
    :projects-failed="projectsFailed"
    :inheritance="inheritance"
    :inheritance-loading="inheritanceLoading"
    :asking="asking"
    :assistant-conversations="assistant.conversations.value"
    :assistant-current-id="assistant.current.value"
    :assistant-title="assistant.title.value"
    :assistant-messages="assistant.messages.value"
    :assistant-streaming="assistant.streaming.value"
    :assistant-tool="assistant.tool.value"
    :assistant-queued="assistant.queued.value"
    :assistant-notice="assistant.notice.value"
    :assistant-credit-refused="assistant.creditRefused.value"
    :assistant-busy="assistant.busy.value"
    :reviewing="reviewing"
    :review-submissions="reviewSubmissions"
    :review-has-more="reviewHasMore"
    :review-loading-more="reviewLoadingMore"
    :review-refreshing="reviewRefreshing"
    :review-total="reviewTotal"
    :review-submitting="reviewSubmitting"
    :available-teams="availableTeams"
    :loading-teams="loadingTeams"
    :joined-teams="joinedTeams"
    :selected-leave-team-id="selectedLeaveTeamId"
    @retry="load"
    @edit="editTask"
    @delete="confirmDeleteTask"
    @claim="onClaim"
    @open-assistant="openAssistant"
    @update:asking="(value: boolean) => (asking = value)"
    @send="askAssistant"
    @stop="assistant.stop().catch(() => undefined)"
    @new-conversation="assistant.startNew"
    @select-conversation="assistant.select"
    @leave="events.emit('leave-clicked')"
    @new-project="createProject"
    @reload-projects="loadProjects"
    @close-review="closeReview"
    @load-more-review="loadMoreReview"
    @submit-review="onReview"
    @cancel-review="onCancelReview"
  />
</template>

<script setup lang="ts">
// 题目详情：上面一块是题目名和这一页的主操作，下面是页签。页签内容是子路由
// （说明 / 我的提交 / 领取者 / 数据），地址各自不变，点了在原地换内容。
// 内容类页签右边带一栏：我的进度（领了才有）和题目信息；领取者与数据是表格和图表，不带。
//
// 这是**容器**：读路由、取题、取参与、取报名、取右栏的项目与「会继承什么」、问芝士、
// 逐版评审、跳转都在这儿，画面交给 `DetailView.vue`。
//
// 领取这条路（实名确认、团队选择、退出）走的是 `useTaskParticipation` 与 `TaskDialogs`：
// 这一页的「领取」按钮只往 `useEvents()` 总线上发 `join-clicked`，不自己发请求。
import type { Project } from '@/cx_types'
import type { TaskParticipationIdentity } from '@/network/api/tasks/types'
import type { TaskSubmission, TaskSubmissionReview } from '@/types'

import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'

import { createEmptyResult, usePaging } from '@/utils/paging'

import { useNewProjectDialog } from '@/composables/useNewProjectDialog'
import { usePageTitle } from '@/composables/usePageTitle'

import { useAssistant } from './composables/useAssistant'
import { useTaskInheritance } from './composables/useTaskInheritance'
import { useTaskData, useTaskManagement, useTaskParticipation, useTeamParticipation } from './composables'
import DetailView from './DetailView.vue'

import { listProjectsForTask } from '@/api'
import { TASK_ROUTE_NAMES } from '@/lib/spaceRouteNames'
import { TasksApi } from '@/network/api/tasks'
import { useEvents } from '@/views/tasks/events'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()

const routeNames = TASK_ROUTE_NAMES
const events = useEvents()
const { setDynamicTitle } = usePageTitle()

const spaceId = computed(() => String(route.params.spaceId))

const taskDataModule = useTaskData()
const { taskId, taskData, loading, error, isTaskCreator, isSpaceAdmin, participationInfo, loadTaskData } =
  taskDataModule

const { onJoinTaskClicked, confirmLeaveTask, handleVerifyInfoSubmit, leaveTaskWithTeam } =
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

// ── 领取 ───────────────────────────────────────────────────────────────────────

const identities = computed(() => participationInfo.value.identities ?? [])
/** 我那一份报名：批过的优先，其次是还在等批的。后端暂时还允许一人多份，这里只取一份。 */
const myIdentity = computed<TaskParticipationIdentity | null>(
  () =>
    identities.value.find((i) => i.approved === 'APPROVED') ??
    identities.value.find((i) => i.approved === 'NONE') ??
    null
)

/** 这一颗按钮不自己发请求：发 `join-clicked`，由 `TaskDialogs` 与 `useTaskParticipation` 接着走
 *  （实名确认、团队选择都在里面）。 */
function onClaim() {
  events.emit('join-clicked')
}

// ── 我自己的那一份进度 ──────────────────────────────────────────────────────────

/** 我最新那一版提交（含判没判）。取不到就当还没交 —— 不编一个版本出来。 */
const myLatest = ref<{ id: number; version: number; review?: TaskSubmissionReview } | null>(null)

async function loadMine() {
  const participantId = myIdentity.value?.id
  if (participantId === undefined || !taskData.value) return
  try {
    const { data } = await TasksApi.listSubmissions(taskData.value.id, participantId, {
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

// ── 右栏：从这道题开出来的项目、建项目会继承什么 ────────────────────────────────

const projects = ref<Project[]>([])
const projectsLoading = ref(false)
const projectsFailed = ref(false)
const { show: showNewProjectDialog } = useNewProjectDialog()

async function loadProjects() {
  const task = taskData.value
  if (!task) return
  projectsLoading.value = true
  projectsFailed.value = false
  try {
    projects.value = (await listProjectsForTask(task.id)).data
  } catch {
    projects.value = []
    projectsFailed.value = true
  } finally {
    projectsLoading.value = false
  }
}

/** 用团队领的，新项目就挂在那个团队下；个人领的让人在对话框里选。 */
function createProject() {
  const task = taskData.value
  if (!task) return
  const teamId = myIdentity.value?.type === 'TEAM' ? myIdentity.value.memberId : null
  showNewProjectDialog(teamId, { id: task.id, name: task.name })
}

watch(
  () => [taskData.value?.id, myIdentity.value?.approved] as const,
  ([, approved]) => {
    if (approved === 'APPROVED') loadProjects()
  },
  { immediate: true }
)

// 「会继承什么」常驻在这里 (#944)：建项目之后，同一份说明还看得到 —— 从这道题
// 新建项目就在右栏那颗按钮上，两份说明放一起，人不必回头找。
const { inheritance, loading: inheritanceLoading } = useTaskInheritance(() => taskData.value?.id)

// ── 逐版评审 ────────────────────────────────────────────────────────────────────
//
// 「领取者」页签点「评审」或「查看提交」，在总线上说要看谁的；从这道题取谁的逐版提交、
// 提交/改/撤回评审都在这儿，对话框挂在 `DetailView` 上。关掉时发 `roster-changed`，
// 那个页签重新取一遍，表里的状态才跟得上。

const reviewing = ref<{ id: number; name: string } | null>(null)
const {
  data: reviewSubmissions,
  refresh: refreshReview,
  loadMore: loadMoreReview,
  hasMore: reviewHasMore,
  refreshing: reviewRefreshing,
  loadingMore: reviewLoadingMore,
  total: reviewTotal,
} = usePaging<TaskSubmission>(async (pageStart) => {
  const task = taskData.value
  const who = reviewing.value
  if (!task || !who) return createEmptyResult<TaskSubmission>()
  const { data } = await TasksApi.listSubmissions(task.id, who.id, {
    allVersions: true,
    sort_by: 'createdAt',
    sort_order: 'desc',
    pageStart: pageStart,
    pageSize: 10,
    queryReview: true,
  })
  return { data: data.submissions, page: data.page }
})

const reviewSubmitting = ref(false)

async function onReview(values: { accepted?: boolean; score?: number; comment?: string }) {
  const latest = reviewSubmissions.value[0]
  const task = taskData.value
  const who = reviewing.value
  if (!latest || !task || !who) return
  reviewSubmitting.value = true
  try {
    if (latest.review && latest.review.reviewed) {
      await TasksApi.patchSubmissionReview(task.id, who.id, latest.id, values)
      toast.success(t('tasks.submissionHistory.reviewUpdated'))
    } else {
      await TasksApi.postSubmissionReview(task.id, who.id, latest.id, values)
      toast.success(t('tasks.submissionHistory.reviewed'))
    }
  } catch (reviewError) {
    toast.error(
      latest.review && latest.review.reviewed
        ? t('tasks.submissionHistory.reviewUpdateFailed')
        : t('tasks.submissionHistory.reviewFailed')
    )
    console.error(reviewError)
  } finally {
    reviewSubmitting.value = false
    refreshReview()
  }
}

async function onCancelReview() {
  const latest = reviewSubmissions.value[0]
  const task = taskData.value
  const who = reviewing.value
  if (!latest?.review || !task || !who) return
  try {
    await TasksApi.deleteSubmissionReview(task.id, who.id, latest.id)
    toast.success(t('tasks.submissionHistory.reviewCanceled'))
  } catch (reviewError) {
    toast.error(t('tasks.submissionHistory.reviewCancelFailed'))
    console.error(reviewError)
  } finally {
    refreshReview()
  }
}

function closeReview() {
  reviewing.value = null
  events.emit('roster-changed')
}

// ── 页签 ───────────────────────────────────────────────────────────────────────

const activeTabName = computed(() => String(route.name ?? ''))
const onSubmitTab = computed(() => route.name === routeNames.submit)
/** 表格和图表那两个页签不带右栏：它们要整宽。 */
const showSide = computed(() => route.name !== routeNames.participants && route.name !== routeNames.insights)

function editTask() {
  router.push({ name: routeNames.edit, params: { spaceId: spaceId.value, taskId: taskId.value } })
}

// ── 问芝士 ─────────────────────────────────────────────────────────────────────
//
// 个人芝士在这道题上的面板（#2285）。宽屏停在右边，把页面挤窄；窄屏从底下升起来。
// 打开面板本身不调用模型，只读这道题上已有的对话。

const assistant = useAssistant(() => taskId.value)
const asking = ref(false)

function openAssistant() {
  asking.value = true
  assistant.load().catch(() => undefined)
}

function askAssistant(text: string) {
  assistant.ask(text, t('tasks.assistant.failed'))
}

// ── 装载 ───────────────────────────────────────────────────────────────────────

async function load() {
  await loadTaskData()
  const task = taskData.value
  if (!task) return
  setDynamicTitle(task.name, 'TaskShell')
  if (task.submitterType === 'TEAM') loadJoinedTeams()
  if (myIdentity.value) loadMine()
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
    reviewing.value = who
  })

  load()
})

watch(reviewing, () => {
  if (reviewing.value) refreshReview()
})
</script>
