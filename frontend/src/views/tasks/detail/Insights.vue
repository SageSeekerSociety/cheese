<template>
  <InsightsView
    :task="taskData"
    :roster="roster"
    :review-by-participant="reviewByParticipant"
    :can-manage="Boolean(isCreator || isAdmin)"
    :loading="loading"
    :failed="failed"
    :failure-reason="failureReason"
    :forbidden="forbidden"
    :progress="progress ?? null"
    :judge-stats="judgeStats"
    @retry="load"
    @judge-stage="loadJudgeStats"
  />
</template>

<script setup lang="ts">
// 题目详情的「数据」页签：出题人和管理员看自己这道题的情况，画面在 `InsightsView.vue`。
//
// 和整板看板的分工：整板看板回答「这个空间怎么样」（管理员的问题），这一页只回答
// 「**我这道题**怎么样」（出题人的问题）。所以这里没有跨题排行、没有分类分布。逐人的
// 名单在「领取者」页签，这里不再列一份。
//
// 数据来自两个真接口，没有一个数字是本地编的：
// - `GET /tasks/{id}/participants` —— 领取名单（含每人加入时刻，走势图就从它算）
// - `GET /spaces/{id}/submissions?taskId=…` —— 这道题的所有提交（含判没判、判过没过）
// - `GET /tasks/{id}/judge/stats` —— 挂着测试点的题：用例通过率、测试分分布、评测累计额度。
//   只有能批改的人读得到；读不到（或没有测试点）就不画那一块，不拖累上面的数。
import type { Task, TaskJudgeStatsView, TaskMembership, TaskProgress, TaskSubmissionReview } from '@/types'

import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import InsightsView from './InsightsView.vue'

import { SpacesApi } from '@/api/spaces'
import { TaskBoardApi } from '@/api/taskBoard'
import { TaskJudgeApi } from '@/api/taskJudge'
import { isForbidden, loadFailureReason } from '@/lib/loadFailure'

const props = defineProps<{
  taskData: Task | null
  isCreator?: boolean
  isAdmin?: boolean
  /** 每个阶段上的进度，题目页取的那一份（全体分布只有批改的人读得到）。 */
  progress?: TaskProgress | null
}>()

const route = useRoute()

const roster = ref<TaskMembership[]>([])
const reviewByParticipant = ref(new Map<number, TaskSubmissionReview | undefined>())
const loading = ref(true)
/** 这两个接口有一个没读到，下面的数字就都不是真的。空名单和「没人领」长得一样，得另记。 */
const loadError = ref<unknown>(null)

// 是「不给你看」还是「这次没读到」，在这里判：画面只拿布尔值、那句话说，不认状态码。
const failed = computed(() => loadError.value !== null)
const failureReason = computed(() => loadFailureReason(loadError.value))
const forbidden = computed(() => isForbidden(loadError.value))

const judgeStats = ref<TaskJudgeStatsView | null>(null)
/** 测试点那一块现在是哪道题、哪个阶段的：换题时清掉，重试时沿用老师选的阶段。 */
let judgeFor: { taskId: number; stage: string | null } | null = null
/** 最后发出去的那一次：先发后到的回答不许盖掉后发的（快速切阶段、换题）。 */
let judgeTicket = 0

/** 测试点那一块单独取：它读不到不该让整页显示「加载失败」。换阶段时也走这里。 */
async function loadJudgeStats(stage?: string | null) {
  const taskId = props.taskData?.id
  const ticket = ++judgeTicket
  if (!taskId || !(props.isCreator || props.isAdmin)) {
    judgeStats.value = null
    judgeFor = null
    return
  }
  if (judgeFor?.taskId !== taskId) judgeStats.value = null
  const wanted = stage !== undefined ? stage : judgeFor?.taskId === taskId ? judgeFor.stage : null
  judgeFor = { taskId, stage: wanted }
  try {
    const got = await TaskJudgeApi.stats(taskId, wanted)
    if (ticket === judgeTicket) judgeStats.value = got
  } catch {
    if (ticket === judgeTicket) judgeStats.value = null
  }
}

async function load() {
  const taskId = props.taskData?.id
  if (!taskId) return
  loading.value = true
  // 上一次的失败不许留到这一次：重新问一次，屏幕上先干净。
  loadError.value = null
  void loadJudgeStats()
  try {
    const [rosterRes, subsRes] = await Promise.all([
      TaskBoardApi.getParticipants(taskId),
      SpacesApi.getSubmissionQueue(Number(route.params.spaceId), { taskId, pageSize: 200 }),
    ])
    roster.value = rosterRes.participants ?? []

    // 同一人可能有多版提交：按 version 取最大的那一版 —— 看的永远是「现在这一版判没判」。
    const byVersion = new Map<number, { version: number; review?: TaskSubmissionReview }>()
    for (const row of subsRes.submissions ?? []) {
      const prev = byVersion.get(row.participantId)
      if (!prev || row.version > prev.version) {
        byVersion.set(row.participantId, { version: row.version, review: row.review })
      }
    }
    reviewByParticipant.value = new Map([...byVersion.entries()].map(([pid, v]) => [pid, v.review]))
  } catch (error) {
    // 名单清空是给「重新试一次」留一张干净的画面，但清空之后屏幕上是一张全 0 的
    // 看板 —— 和「这道题确实没人领」分不出来，所以这个错要单独交出去。
    roster.value = []
    reviewByParticipant.value = new Map()
    loadError.value = error
  } finally {
    loading.value = false
  }
}

watch(() => props.taskData?.id, load, { immediate: true })
</script>
