<script setup lang="ts">
// 题目详情：题目本身、领取、我的进度；出题人和管理员还看得到领取者名单与领取走势。
//
// 领取这条路（实名确认、团队选择、退出）走的是 `useTaskParticipation` 与 `TaskDialogs`：
// 这一页的「领取」按钮只往 `useEvents()` 总线上发 `join-clicked`，不自己发请求。
// 下面四格（提交记录 / 参与者 / 交作业 / 启星研导）是子路由，在 `router-view` 里。
import type { TaskMembership, TaskSubmissionReview } from '@/types'

import { computed, defineAsyncComponent, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { usePageTitle } from '@/composables/usePageTitle'

import TaskEligibilityAlerts from './components/TaskEligibilityAlerts.vue'
import TaskProjectsCard from './components/TaskProjectsCard.vue'

import { MarkdownRenderer } from '@/components/chat/services/markdownRenderer'
import PageHeader from '@/components/common/PageHeader.vue'
import PanelCard from '@/components/spaces/PanelCard.vue'
import TrendChart from '@/components/spaces/TrendChart.vue'
import TaskAttachmentList from '@/components/tasks/TaskAttachmentList.vue'
import TaskSubmissionHistory from '@/components/tasks/TaskSubmissionHistory.vue'
import { TASK_ROUTE_NAMES } from '@/lib/spaceRouteNames'
import { SpacesApi } from '@/network/api/spaces'
import { TasksApi } from '@/network/api/tasks'
import { splitOrigin } from '@/views/spaces/model'
import { AIChatButton, LoadingErrorContainer, TaskDialogs } from '@/views/tasks/components'
import {
  useAIChat,
  useTaskData,
  useTaskManagement,
  useTaskParticipation,
  useTeamParticipation,
} from '@/views/tasks/composables'
import { useEvents } from '@/views/tasks/events'

const route = useRoute()
const router = useRouter()

const routeNames = TASK_ROUTE_NAMES
const events = useEvents()
const { setDynamicTitle } = usePageTitle()

const spaceId = computed(() => String(route.params.spaceId))
const homeTo = computed(() => ({ name: 'SpacesDetailTasksList', params: { spaceId: spaceId.value } }))

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
const { chatDialogOpen, selectedContext, openGeneralChat } = useAIChat()
const { confirmDeleteTask } = useTaskManagement(taskDataModule)

// ── 题目本身 ────────────────────────────────────────────────────────────────────

/** 简介里那一段「出处」（`〔…〕`）是老库把来源写进 `intro` 的约定，`splitOrigin` 把
 *  它摘出来。摘要与出处是同一段文本的两半，所以两个都由它给，不各填各的。 */
const intro = computed(() => splitOrigin(taskData.value?.intro ?? ''))
const publisherName = computed(() => taskData.value?.creator.nickname || taskData.value?.creator.username || '')
const canEdit = computed(() => isTaskCreator.value || isSpaceAdmin.value)

/** 视频：只嵌 B 站，别的平台给一个（过了 https 校验的）链接，不假装能放。 */
const videoLink = computed(() => {
  const url = taskData.value?.videoUrl
  if (!url) return null
  try {
    if (new URL(url).protocol === 'https:') return url
  } catch {
    // 不是个 URL
  }
  return null
})
const videoEmbed = computed(() => {
  const bvid = videoLink.value?.match(/bilibili\.com\/video\/(BV[\w]+)/)?.[1]
  return bvid ? `//player.bilibili.com/player.html?bvid=${bvid}&autoplay=0` : null
})

/** 题目详情是富文本（TipTap JSON 或 Markdown）—— 与老概览同一段判断，同一套渲染。 */
const markdownRenderer = new MarkdownRenderer()
const TipTapViewer = defineAsyncComponent(() => import('@/components/common/Editor/TipTapViewer.vue'))
const isTipTapJson = computed(() => {
  const raw = taskData.value?.description ?? ''
  if (!raw) return false
  try {
    const parsed = JSON.parse(raw)
    return typeof parsed === 'object' && parsed !== null && parsed.type === 'doc'
  } catch {
    return false
  }
})
const tipTapContent = computed(() => {
  try {
    return JSON.parse(taskData.value?.description ?? '{}')
  } catch {
    return { type: 'doc', content: [] }
  }
})
const renderedMarkdown = computed(() => {
  const raw = taskData.value?.description ?? ''
  if (!raw || isTipTapJson.value) return ''
  return markdownRenderer.render(raw)
})

// ── 领取那一段的四种状态 ────────────────────────────────────────────────────────
//
// 判据全部是接口给的事实：这道题的审核状态、我的报名记录的审核状态、截止/开始时刻、
// 领取人数与上限。

const identities = computed(() => participationInfo.value.identities ?? [])
const joined = computed(
  () => taskData.value?.joined === true || identities.value.some((i) => i.approved === 'APPROVED')
)
const myClaimPending = computed(() => identities.value.some((i) => i.approved === 'NONE'))
const myClaimRejected = computed(
  () => identities.value.length > 0 && identities.value.every((i) => i.approved === 'DISAPPROVED')
)

/** `participantLimit` 的 0 是「不限」，不是「一个人都不许」。 */
const limit = computed(() => ((taskData.value?.participantLimit ?? 0) > 0 ? taskData.value!.participantLimit : null))
const claimCount = computed(() => taskData.value?.participants.total ?? 0)
const deadlinePassed = computed(() => {
  const at = taskData.value?.deadline
  return at != null && at < Date.now()
})
const notStarted = computed(() => {
  const at = taskData.value?.registrationStartAt
  return at != null && at > Date.now()
})

const claim = computed(() => {
  const task = taskData.value
  if (!task) return null
  if (myClaimPending.value) return { label: '待审核', disabled: true, tone: 'info' }
  if (myClaimRejected.value) return { label: '已驳回', disabled: true, tone: 'error' }
  if (task.approved === 'NONE') return { label: '待审核', disabled: true, tone: 'info' }
  if (task.approved === 'DISAPPROVED') return { label: '已驳回', disabled: true, tone: 'error' }
  if (joined.value) return { label: '你已经领取', disabled: true, tone: 'success' }
  if (deadlinePassed.value) return { label: '已截止', disabled: true, tone: 'default' }
  if (notStarted.value) return { label: '还没开始', disabled: true, tone: 'default' }
  if (limit.value !== null && claimCount.value >= limit.value)
    return { label: '人数已满', disabled: true, tone: 'default' }
  return { label: '领取这道题', disabled: false, tone: 'primary' }
})

/** 这一颗按钮不自己发请求：发 `join-clicked`，由 `TaskDialogs` 与 `useTaskParticipation` 接着走
 *  （实名确认、团队选择都在里面）。 */
function onClaim() {
  if (claim.value?.disabled) return
  events.emit('join-clicked')
}

const submitTo = computed(() => ({ name: routeNames.submit, params: { spaceId: spaceId.value, taskId: taskId.value } }))
const submissionsTo = computed(() => ({
  name: routeNames.submissions,
  params: { spaceId: spaceId.value, taskId: taskId.value },
}))
const participantsTo = computed(() => ({
  name: routeNames.participants,
  params: { spaceId: spaceId.value, taskId: taskId.value },
}))
const insightsTo = computed(() => ({
  name: routeNames.insights,
  params: { spaceId: spaceId.value, taskId: taskId.value },
}))
const aiAdviceTo = computed(() => ({
  name: routeNames.aiAdvice,
  params: { spaceId: spaceId.value, taskId: taskId.value },
}))

function editTask() {
  router.push({ name: routeNames.edit, params: { spaceId: spaceId.value, taskId: taskId.value } })
}

// ── 我自己的那一份进度 ──────────────────────────────────────────────────────────

type ClaimStatus = 'IN_PROGRESS' | 'SUBMITTED' | 'PASSED' | 'REJECTED'

const CLAIM_LABEL: Record<ClaimStatus, string> = {
  IN_PROGRESS: '进行中',
  SUBMITTED: '已提交',
  PASSED: '已通过',
  REJECTED: '未通过',
}

/** 我最新那一版提交（含判没判）。取不到时这一行不显示 —— 不编一个「进行中」出来。 */
const myLatest = ref<{ id: number; version: number; review?: TaskSubmissionReview } | null>(null)
const myStatus = computed<ClaimStatus>(() => {
  if (!myLatest.value) return 'IN_PROGRESS'
  if (!myLatest.value.review) return 'SUBMITTED'
  return myLatest.value.review.detail?.accepted ? 'PASSED' : 'REJECTED'
})

async function loadMine() {
  const participantId = identities.value.find((i) => i.approved === 'APPROVED')?.id ?? identities.value[0]?.id
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

// ── 出题人视角：领取者名单、提交与通过的人数、领取走势 ────────────────────────────
//
// 名单接口由服务端按 `may_teach_task` 把关（不是前端藏起来的），拿不到就 403 ——
// 那时这一页不画名单、不画走势、不数提交，只写明为什么。

type RosterRow = {
  id: number
  name: string
  teamSize: number
  createdAt: number
  status: ClaimStatus
  /** 最新那一版提交的主键；没有提交就是 null（评审按钮据此决定显不显示）。 */
  submissionId: number | null
  review?: TaskSubmissionReview
}

const roster = ref<TaskMembership[]>([])
const latestByParticipant = ref(new Map<number, { submissionId: number; review?: TaskSubmissionReview }>())
const rosterDenied = ref(false)

const canManage = computed(() => isTaskCreator.value || isSpaceAdmin.value)
const canSeeRoster = computed(() => canManage.value && !rosterDenied.value)

async function loadRoster() {
  if (!canManage.value || !taskData.value) return
  try {
    const [rosterRes, subsRes] = await Promise.all([
      TasksApi.getParticipants(taskData.value.id),
      SpacesApi.getSubmissionQueue(Number(spaceId.value), { taskId: taskData.value.id, pageSize: 200 }),
    ])
    roster.value = rosterRes.data.participants ?? []

    // 同一人可能有多版：按 `version` 取最大的那一版 —— 要回答的永远是「现在这一版判没判」。
    const byVersion = new Map<number, { submissionId: number; version: number; review?: TaskSubmissionReview }>()
    for (const row of subsRes.data.submissions ?? []) {
      const prev = byVersion.get(row.participantId)
      if (!prev || row.version > prev.version) {
        byVersion.set(row.participantId, { submissionId: row.id, version: row.version, review: row.review })
      }
    }
    latestByParticipant.value = new Map(
      [...byVersion.entries()].map(([pid, v]) => [pid, { submissionId: v.submissionId, review: v.review }])
    )
  } catch {
    // 403 是这条接口对无权者的正常回答，不是错误页该出现的东西。
    rosterDenied.value = true
  }
}

/** 被驳回的报名不算「领取者」—— 这一列真库是 `TaskMembership.approved`。 */
const claimed = computed(() => roster.value.filter((r) => r.approved !== 'DISAPPROVED'))

function statusOf(participantId: number): ClaimStatus {
  const found = latestByParticipant.value.get(participantId)
  if (!found || !found.review) return found ? 'SUBMITTED' : 'IN_PROGRESS'
  return found.review.detail?.accepted ? 'PASSED' : 'REJECTED'
}

/** 按领取时间倒序 —— 最近领的在最上面。 */
const ROSTER = computed<RosterRow[]>(() =>
  [...claimed.value]
    .sort((a, b) => b.createdAt - a.createdAt)
    .map((m) => {
      const found = latestByParticipant.value.get(m.id)
      return {
        id: m.id,
        name: m.member?.name ?? '—',
        teamSize: m.teamMembers?.length ?? 0,
        createdAt: m.createdAt,
        status: statusOf(m.id),
        submissionId: found?.submissionId ?? null,
        review: found?.review,
      }
    })
)

const passedCount = computed(() => ROSTER.value.filter((r) => r.status === 'PASSED').length)
const submittedCount = computed(() => ROSTER.value.filter((r) => r.status !== 'IN_PROGRESS').length)

/** 等判的那几个人：交了但还没评审结果 —— 出题人这一屏上最该点的那几行。 */
const awaitingReview = computed(() => ROSTER.value.filter((r) => r.status === 'SUBMITTED').length)

const reviewingRowId = ref<number | null>(null)
async function review(row: RosterRow, accepted: boolean) {
  if (row.submissionId === null || !taskData.value) return
  reviewingRowId.value = row.id
  try {
    // 这一颗只判过没过，所以评分这一格是 0（未评分）—— 要写评分与评论请走「逐版评审」
    // 那张对话框（`TaskSubmissionHistory`），它连着真接口的评分/评论/撤销评审。
    await TasksApi.postSubmissionReview(taskData.value.id, row.id, row.submissionId, {
      accepted,
      score: 0,
      comment: '',
    })
    await loadRoster()
    await loadMine()
  } catch (err) {
    console.error('评审失败', err)
  } finally {
    reviewingRowId.value = null
  }
}

/** 逐版评审那张对话框：一版一版地看、带评分与评论、判错了还能撤销。 */
const historyFor = ref<RosterRow | null>(null)

/** 最近 12 天的累计领取 —— 从每人的加入时刻数出来。窗口之前就领了的人算作第 0 天的
 *  底数，否则走势会假装他们不存在（`TaskInsights.vue` 用的是同一个算法）。 */
const DAY_LABELS = computed(() =>
  Array.from({ length: 12 }, (_, i) => {
    const d = new Date(Date.now() - (11 - i) * 86_400_000)
    return `${d.getMonth() + 1}/${d.getDate()}`
  })
)
const claimTrend = computed(() => {
  const start = Date.now() - 12 * 86_400_000
  const base = claimed.value.filter((r) => r.createdAt < start).length
  return Array.from({ length: 12 }, (_, i) => {
    const dayEnd = start + (i + 1) * 86_400_000
    return base + claimed.value.filter((r) => r.createdAt >= start && r.createdAt < dayEnd).length
  })
})

// ── 右栏的几行事实 ──────────────────────────────────────────────────────────────

function deadlineText(): string {
  const at = taskData.value?.deadline
  if (at == null) return '不限截止'
  const days = Math.ceil((at - Date.now()) / 86_400_000)
  if (days < 0) return `已截止 ${-days} 天`
  if (days === 0) return '今天截止'
  return `${days} 天后截止`
}

const formText = computed(() => {
  const t = taskData.value
  if (!t) return ''
  if (t.submitterType === 'USER') return '个人'
  const min = t.minTeamSize ?? 1
  const max = t.maxTeamSize ?? 1
  return min === max ? `小队 ${min} 人` : `小队 ${min}–${max} 人`
})

/** 难度：1–5 星；老数据没有难度时不画这一行。 */
const rank = computed(() => taskData.value?.rank ?? 0)

/** 领取之后给我的提交期限（天）；0 或没有就不画这一行。 */
const defaultDeadline = computed(() => taskData.value?.defaultDeadline ?? 0)

const CountdownTimer = defineAsyncComponent(() => import('@/components/common/CountdownTimer.vue'))

const progress = computed(() => (limit.value === null ? 0 : Math.min(100, (claimCount.value / limit.value) * 100)))

// ── 装载 ───────────────────────────────────────────────────────────────────────

async function load() {
  await loadTaskData()
  const task = taskData.value
  if (!task) return
  setDynamicTitle(task.name, routeNames.detail)
  if (task.submitterType === 'TEAM') loadJoinedTeams()
  if (canManage.value) loadRoster()
  if (joined.value) loadMine()
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

  load()
})
</script>

<template>
  <PageHeader>
    <v-btn variant="text" size="small" prepend-icon="mdi-arrow-left" :to="homeTo">
      {{ taskData?.space?.name ?? '题目' }}
    </v-btn>
    <v-chip v-if="intro.origin" size="x-small" label variant="tonal" color="info" class="td__origin">
      {{ intro.origin }}
    </v-chip>
    <v-spacer />
    <v-btn v-if="canEdit" variant="text" size="small" icon="mdi-pencil" @click="editTask" />
    <v-btn v-if="canEdit" variant="text" size="small" icon="mdi-delete" @click="confirmDeleteTask" />
  </PageHeader>

  <LoadingErrorContainer v-if="loading || error" :loading="loading" :error="error" @retry="load" />

  <div v-else-if="taskData" class="td">
    <TaskEligibilityAlerts :task="taskData" class="td__eligibility" />
    <div class="td__grid">
      <div class="td__main">
        <!-- 题目那一张卡：标题、出题人、标签、驳回原因、视频、材料、领取 -->
        <PanelCard>
          <div class="td__meta">
            <v-chip
              size="x-small"
              label
              variant="tonal"
              :color="
                taskData.approved === 'APPROVED' ? 'success' : taskData.approved === 'DISAPPROVED' ? 'error' : 'info'
              "
            >
              {{
                taskData.approved === 'APPROVED' ? '已过审' : taskData.approved === 'DISAPPROVED' ? '未过审' : '待审核'
              }}
            </v-chip>
            <v-chip v-if="taskData.category" size="x-small" label variant="tonal">
              {{ taskData.category.name }}
            </v-chip>
            <v-chip v-if="intro.origin" size="x-small" label variant="tonal" color="info">{{ intro.origin }}</v-chip>
            <v-spacer />
            <span class="td__publisher">出题人 · {{ publisherName }}</span>
          </div>

          <h1 class="td__title">{{ taskData.name }}</h1>
          <p v-if="intro.summary" class="td__summary">{{ intro.summary }}</p>

          <!-- 真库这一层没有自由标签，题目挂的是 topics（名字来自接口的 `queryTopics`）。 -->
          <div v-if="taskData.topics?.length" class="td__tags">
            <v-chip v-for="topic in taskData.topics" :key="topic.id" size="x-small" label variant="text">
              #{{ topic.name }}
            </v-chip>
          </div>

          <!-- 驳回原因：接口给的是审核那一刻写下的那一段，没有就不显示，不补一句套话。 -->
          <v-alert
            v-if="taskData.approved === 'DISAPPROVED' && taskData.rejectReason"
            type="error"
            variant="tonal"
            density="comfortable"
            class="td__reject"
          >
            审核未通过：{{ taskData.rejectReason }}
          </v-alert>

          <!-- 视频：只嵌 B 站；别的平台给一条（过了 https 校验的）链接，不假装能放。 -->
          <section v-if="videoLink" class="td__block">
            <iframe
              v-if="videoEmbed"
              :src="videoEmbed"
              title="题目视频"
              frameborder="0"
              allowfullscreen
              referrerpolicy="no-referrer"
              class="td__video"
            />
            <template v-else>
              <v-alert type="warning" variant="tonal" density="compact" class="mb-2">
                只支持嵌入 B 站视频，其他链接在新窗口打开
              </v-alert>
              <a :href="videoLink" target="_blank" rel="noopener" class="td__link">{{ videoLink }}</a>
            </template>
          </section>

          <!-- 材料：清单与下载次数都是服务端的（`TaskAttachmentList`），点不点得动也由它说。 -->
          <section class="td__block">
            <h2 class="td__h2">材料</h2>
            <TaskAttachmentList :task-id="taskData.id" />
          </section>

          <!-- 领取 -->
          <section class="td__block td__claim">
            <v-btn
              :color="claim?.tone === 'default' ? undefined : claim?.tone"
              :variant="claim?.disabled ? 'tonal' : 'flat'"
              :disabled="claim?.disabled"
              size="large"
              class="td__claim-btn"
              @click="onClaim"
            >
              {{ claim?.label }}
            </v-btn>

            <v-btn v-if="joined" variant="text" :to="submitTo" prepend-icon="mdi-upload">提交作业</v-btn>
            <v-btn v-if="joined" variant="text" @click="events.emit('leave-clicked')">退出这道题</v-btn>
            <v-btn v-if="joined" variant="text" :to="submissionsTo" prepend-icon="mdi-tray-full">我的提交记录</v-btn>

            <!-- 我自己的状态：进行中 / 已提交 / 已通过 / 未通过 —— 从我最新那一版提交与它的
                 评审结果算出来，不是 `completion_status`（那一列服务端自己都说不准）。 -->
            <p v-if="joined" class="td__mine">
              我的状态 · <strong :class="`td__mine--${myStatus.toLowerCase()}`">{{ CLAIM_LABEL[myStatus] }}</strong>
              <template v-if="myLatest?.version"> · 第 {{ myLatest.version }} 版</template>
            </p>
          </section>
        </PanelCard>

        <!-- 题目详情（富文本）：老概览撤了，这一段内容得有地方落。 -->
        <PanelCard title="题目详情">
          <div class="td__description">
            <TipTapViewer v-if="isTipTapJson" :value="tipTapContent" />
            <div v-else-if="renderedMarkdown" class="markdown-body" v-html="renderedMarkdown" />
            <p v-else class="td__note">暂无详情</p>
          </div>
        </PanelCard>

        <!-- 出题人视角的领取者名单：逐人一行，通过的画「通过」、没判的画两颗按钮 -->
        <PanelCard
          v-if="canSeeRoster"
          title="领取者"
          :subtitle="`${ROSTER.length} 人 · 按领取时间倒序${awaitingReview ? ` · ${awaitingReview} 人等着判` : ''}`"
        >
          <ul v-if="ROSTER.length" class="roster">
            <li v-for="row in ROSTER" :key="row.id">
              <v-avatar size="26" class="roster__avatar">{{ row.name.slice(0, 1) }}</v-avatar>
              <span class="roster__name">{{ row.name }}</span>
              <span v-if="row.teamSize" class="roster__team">小队 {{ row.teamSize }} 人</span>
              <v-spacer />
              <v-chip size="x-small" label variant="tonal" :class="`claim-${row.status.toLowerCase()}`">
                {{ CLAIM_LABEL[row.status] }}
              </v-chip>
              <template v-if="row.status === 'SUBMITTED'">
                <v-btn
                  size="x-small"
                  variant="tonal"
                  color="success"
                  :loading="reviewingRowId === row.id"
                  @click="review(row, true)"
                >
                  通过
                </v-btn>
                <v-btn
                  size="x-small"
                  variant="tonal"
                  color="error"
                  :loading="reviewingRowId === row.id"
                  @click="review(row, false)"
                >
                  不通过
                </v-btn>
              </template>
              <v-btn
                v-if="row.submissionId !== null"
                size="x-small"
                variant="text"
                icon="mdi-history"
                title="逐版评审"
                @click="historyFor = row"
              />
            </li>
          </ul>
          <p v-else class="td__note">暂无领取</p>
        </PanelCard>

        <!-- 那四格老页面：提交记录 / 参与者 / 交作业 / 启星研导。交完作业 `Submit.vue`
             会 push 到 `routeNames.submissions`，所以这几条路由必须留在原处。 -->
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

      <aside class="td__side">
        <PanelCard title="领取进度">
          <p class="td__count">
            <strong>{{ claimCount }}</strong>
            <span v-if="limit"> / {{ limit }} 人</span>
            <span v-else> 人 · 不限人数</span>
          </p>
          <v-progress-linear v-if="limit" :model-value="progress" height="6" rounded color="primary" class="mb-3" />

          <dl class="td__facts">
            <div>
              <dt>提交</dt>
              <dd>{{ canSeeRoster ? `${submittedCount} 份` : '—' }}</dd>
            </div>
            <div>
              <dt>通过</dt>
              <dd>{{ canSeeRoster ? `${passedCount} 份` : '—' }}</dd>
            </div>
            <div>
              <dt>截止</dt>
              <dd>{{ deadlineText() }}</dd>
            </div>
            <div>
              <dt>形式</dt>
              <dd>{{ formText }}</dd>
            </div>
            <div v-if="rank">
              <dt>难度</dt>
              <dd><v-rating :model-value="rank" readonly density="compact" size="x-small" /></dd>
            </div>
            <div>
              <dt>提交次数</dt>
              <dd>{{ taskData.resubmittable ? '可多次提交' : '仅可提交一次' }}</dd>
            </div>
            <div v-if="defaultDeadline">
              <dt>提交期限</dt>
              <dd>领取后 {{ defaultDeadline }} 天</dd>
            </div>
            <div v-if="joined && taskData.userDeadline">
              <dt>我的截止</dt>
              <dd><CountdownTimer :deadline="taskData.userDeadline" label="" /></dd>
            </div>
          </dl>
        </PanelCard>

        <PanelCard v-if="canSeeRoster" title="领取走势" subtitle="最近 12 天，累计领取人数">
          <TrendChart
            v-if="claimCount"
            :labels="DAY_LABELS"
            :series="[{ name: '累计领取', values: claimTrend }]"
            :height="180"
          />
          <p v-else class="td__note">暂无领取</p>
        </PanelCard>

        <TaskProjectsCard :task="taskData" :participation-info="participationInfo" />

        <v-btn variant="text" block :to="aiAdviceTo" prepend-icon="mdi-robot" class="td__side-link">启星研导</v-btn>
        <v-btn
          v-if="canSeeRoster"
          variant="text"
          block
          :to="insightsTo"
          prepend-icon="mdi-chart-line"
          class="td__side-link"
        >
          单题分析
        </v-btn>
        <v-btn
          v-if="canEdit"
          variant="text"
          block
          :to="participantsTo"
          prepend-icon="mdi-account-group"
          class="td__side-link"
        >
          参与者管理
        </v-btn>
      </aside>
    </div>
  </div>

  <v-dialog :model-value="historyFor !== null" max-width="860" scrollable @update:model-value="historyFor = null">
    <TaskSubmissionHistory
      v-if="historyFor && taskData"
      :task-id="taskData.id"
      :participant-id="historyFor.id"
      :reviewable="true"
      :is-dialog="true"
      :outlined="true"
      :highlight-latest="true"
      :title="`逐版评审 · ${historyFor.name}`"
    />
  </v-dialog>

  <AIChatButton :open="!chatDialogOpen" @click="openGeneralChat" />

  <!-- 领取/退队/实名那几张对话框：老机器，事件总线上接了它。 -->
  <TaskDialogs
    :task-data="taskData"
    :available-teams="availableTeams"
    :loading-teams="loadingTeams"
    :joined-teams="joinedTeams"
    :selected-leave-team-id="selectedLeaveTeamId"
    :selected-context="selectedContext"
    :participation-info="participationInfo"
  />
</template>

<style scoped lang="scss">
.td__origin {
  margin-left: 8px;
}

.td__grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 320px;
  gap: 16px;
  align-items: start;
}

@media (max-width: 1000px) {
  .td__grid {
    grid-template-columns: 1fr;
  }
}

.td__main,
.td__side {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}

.td__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.td__publisher {
  color: rgba(var(--v-theme-on-surface), 0.55);
  font-size: 0.78rem;
}

.td__title {
  margin: 12px 0 0;
  font-size: 1.5rem;
  font-weight: 650;
  line-height: 1.35;
}

.td__summary {
  margin: 8px 0 0;
  color: rgba(var(--v-theme-on-surface), 0.72);
  font-size: 0.9rem;
  line-height: 1.7;
  white-space: pre-wrap;
}

.td__tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 8px;
}

.td__reject {
  margin-top: 14px;
}

.td__block {
  padding-top: 14px;
  margin-top: 16px;
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.07);
}

.td__h2 {
  margin: 0 0 10px;
  font-size: 0.9rem;
  font-weight: 600;
}

.td__video {
  width: 100%;
  aspect-ratio: 16 / 9;
  border-radius: 8px;
}

.td__link {
  color: rgb(var(--v-theme-primary));
  font-size: 0.82rem;
  word-break: break-all;
}

.td__claim {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
}

.td__claim-btn {
  min-width: 150px;
}

.td__mine {
  margin: 0;
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 0.8rem;
}

.td__mine--in_progress {
  color: rgba(var(--v-theme-on-surface), 0.75);
}

.td__mine--submitted {
  color: rgb(var(--v-theme-warning));
}

.td__mine--passed {
  color: rgb(var(--v-theme-success));
}

.td__mine--rejected {
  color: rgb(var(--v-theme-error));
}

.td__description {
  font-size: 0.9rem;
  line-height: 1.75;
}

.td__count {
  margin: 0 0 10px;
  font-size: 0.85rem;
  color: rgba(var(--v-theme-on-surface), 0.7);
}

.td__count strong {
  font-size: 1.6rem;
  font-weight: 650;
  color: rgb(var(--v-theme-on-surface));
}

.td__facts {
  margin: 0;
}

.td__facts > div {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  padding: 7px 0;
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.05);
}

.td__facts > div:first-child {
  border-top: none;
}

.td__facts dt {
  color: rgba(var(--v-theme-on-surface), 0.55);
  font-size: 0.8rem;
}

.td__facts dd {
  margin: 0;
  font-size: 0.82rem;
}

.td__note {
  margin: 10px 0 0;
  color: rgba(var(--v-theme-on-surface), 0.55);
  font-size: 0.76rem;
  line-height: 1.7;
}

.td__side-link {
  justify-content: flex-start;
}

.td__eligibility {
  margin-bottom: 16px;
}

.roster {
  padding: 0;
  margin: 0;
  list-style: none;
}

.roster li {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 9px 0;
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.05);
}

.roster li:first-child {
  border-top: none;
}

.roster__avatar {
  color: rgba(var(--v-theme-on-surface), 0.8);
  font-size: 0.72rem;
  background: rgba(var(--v-theme-on-surface), 0.1);
}

.roster__name {
  font-size: 0.85rem;
}

.roster__team {
  padding: 1px 8px;
  color: rgba(var(--v-theme-on-surface), 0.55);
  font-size: 0.72rem;
  background: rgba(var(--v-theme-on-surface), 0.06);
  border-radius: 999px;
}

.claim-in_progress {
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.claim-submitted {
  color: rgb(var(--v-theme-warning));
}

.claim-passed {
  color: rgb(var(--v-theme-success));
}

.claim-rejected {
  color: rgb(var(--v-theme-error));
}
</style>
