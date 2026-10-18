<script setup lang="ts">
/**
 * 后台的「CI 与合并队列」（`/admin/ci-activity`）：平台自己的 Actions 机器和合并队列
 * 这段时间在做什么。
 *
 * 这一层是**容器**：取数，画面在 `AdminCiActivityPageView.vue`（配对视图的名字由
 * `pnpm run lint:scenes` 按页面文件名机械算出）。三份数各取各的：整页的数随窗口换；
 * 作业表随窗口和选中的机器换；步骤表随选中的作业换。三份都只读后端的表，不碰 GitHub
 * —— 后端每几分钟扫一次（`domain/ci_activity/ingest.py`），这一页没有刷新按钮。
 */
import type { CiBoard, CiJobRow, CiStepRow } from '@/views/admin/ciActivityApi'

import { onMounted, ref } from 'vue'

import AdminCiActivityPageView from '@/views/admin/AdminCiActivityPageView.vue'
import { getCiBoard, getCiJobs, getCiSteps } from '@/views/admin/ciActivityApi'

defineOptions({ name: 'AdminCiActivityPage' })

const days = ref(7)
const board = ref<CiBoard | null>(null)
const loading = ref(true)
const failed = ref(false)
const runner = ref<string | null>(null)
const jobs = ref<CiJobRow[]>([])
const jobsLoading = ref(false)
const job = ref<string | null>(null)
const steps = ref<CiStepRow[]>([])
const stepsLoading = ref(false)

/** 每一次读都带一个序号：窗口连点两下时，先发的那次晚回来不能盖掉后发的。 */
let boardSeq = 0
let jobsSeq = 0
let stepsSeq = 0

async function loadBoard() {
  const seq = ++boardSeq
  loading.value = true
  failed.value = false
  try {
    const next = await getCiBoard(days.value)
    if (seq === boardSeq) board.value = next
  } catch {
    if (seq === boardSeq) failed.value = true
  } finally {
    if (seq === boardSeq) loading.value = false
  }
}

async function loadJobs() {
  const seq = ++jobsSeq
  jobsLoading.value = true
  try {
    const next = await getCiJobs(days.value, runner.value)
    if (seq === jobsSeq) jobs.value = next.jobs
  } catch {
    if (seq === jobsSeq) jobs.value = []
  } finally {
    if (seq === jobsSeq) jobsLoading.value = false
  }
}

async function loadSteps() {
  const seq = ++stepsSeq
  steps.value = []
  if (!job.value) return
  stepsLoading.value = true
  try {
    const next = await getCiSteps(days.value, job.value, runner.value)
    if (seq === stepsSeq) steps.value = next.steps
  } catch {
    if (seq === stepsSeq) steps.value = []
  } finally {
    if (seq === stepsSeq) stepsLoading.value = false
  }
}

function setDays(next: number) {
  if (next === days.value) return
  days.value = next
  void loadBoard()
  void loadJobs()
  void loadSteps()
}

function setRunner(next: string | null) {
  runner.value = next
  job.value = null
  steps.value = []
  void loadJobs()
}

function setJob(next: string | null) {
  job.value = next
  void loadSteps()
}

onMounted(() => {
  void loadBoard()
  void loadJobs()
})
</script>

<template>
  <AdminCiActivityPageView
    :board="board"
    :days="days"
    :loading="loading"
    :failed="failed"
    :jobs="jobs"
    :jobs-loading="jobsLoading"
    :runner="runner"
    :steps="steps"
    :steps-loading="stepsLoading"
    :job="job"
    @set-days="setDays"
    @set-runner="setRunner"
    @set-job="setJob"
    @retry="loadBoard"
  />
</template>
