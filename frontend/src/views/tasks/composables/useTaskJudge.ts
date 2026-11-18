/**
 * 测试点评测的取数：一版提交的评测结果、提交页那块「将要评测的代码」、参与者列表的评测
 * 摘要，以及出题人试跑。组件不够 API 层（`.claude/rules/architecture.md`），都从这里拿。
 *
 * 评测是排队跑的，学生交完要等一会儿：结果还在排队或在跑时每隔几秒再读一次，跑完就停；
 * 组件卸载时也停（`onScopeDispose`）。
 */
import type {
  TaskCodeVersion,
  TaskJudgeRun,
  TaskJudgeSummary,
  TaskRejudgeCounts,
  TaskSelfTestView,
  TaskTestSuite,
  TaskTestTrial,
  TaskTestTrialFile,
} from '@/types'

import { onScopeDispose, provide, ref } from 'vue'

import { TaskJudgeApi } from '@/api/taskJudge'
import { editable } from '@/lib/taskTestSuite'
import { TEST_TRIAL } from '@/lib/testTrial'

/** 还没跑完的状态：再读一次。 */
export const inFlight = (run: TaskJudgeRun | null | undefined) => run?.state === 'queued' || run?.state === 'running'

/** 隔多久再读一次还在跑的评测。 */
export const POLL_MS = 3000

/** 一个会自己停下来的轮询：`tick` 返回 true 就继续。 */
function poller(tick: () => Promise<boolean>, every: number) {
  let timer: ReturnType<typeof setTimeout> | null = null
  let stopped = false
  const stop = () => {
    stopped = true
    if (timer) clearTimeout(timer)
    timer = null
  }
  const start = () => {
    stop()
    stopped = false
    const step = async () => {
      let again = false
      try {
        again = await tick()
      } catch {
        // 读一次失败不算结论：下一轮再读。
        again = true
      }
      if (again && !stopped) timer = setTimeout(step, every)
    }
    void step()
  }
  onScopeDispose(stop)
  return { start, stop }
}

/** 一版提交的评测结果；还在跑时每隔几秒再读。`submissionId` 为 0 时不读。 */
export function useSubmissionJudge(ids: {
  taskId: () => number
  participantId: () => number
  submissionId: () => number
}) {
  const judge = ref<TaskJudgeRun | null>(null)
  const loaded = ref(false)
  const failed = ref(false)

  const { start, stop } = poller(async () => {
    const submissionId = ids.submissionId()
    if (!submissionId) return false
    try {
      const res = await TaskJudgeApi.submissionJudge(ids.taskId(), ids.participantId(), submissionId)
      judge.value = res.judge
      failed.value = false
    } catch {
      failed.value = true
      return false
    } finally {
      loaded.value = true
    }
    return inFlight(judge.value)
  }, POLL_MS)

  function load() {
    judge.value = null
    loaded.value = false
    start()
  }

  return { judge, loaded, failed, load, stop }
}

/** 这条领题的项目默认分支现在的那一版；读不到是 null（提交页据此留空，不拦提交）。 */
export async function readCodeVersion(taskId: number, participantId: number): Promise<TaskCodeVersion | null> {
  try {
    return (await TaskJudgeApi.codeVersion(taskId, participantId)).codeVersion
  } catch {
    return null
  }
}

/** 几版提交的评测摘要，按提交 id 归位；读不到就是空的（那一列显示「—」）。 */
export async function readJudgeSummaries(
  taskId: number,
  submissionIds: number[]
): Promise<Map<number, TaskJudgeSummary>> {
  const ids = [...new Set(submissionIds.filter((id) => id > 0))]
  if (!ids.length) return new Map()
  const out = new Map<number, TaskJudgeSummary>()
  // 后端一次最多收 `SUMMARY_BATCH` 版：多了分几次问。
  for (let at = 0; at < ids.length; at += TaskJudgeApi.SUMMARY_BATCH) {
    try {
      const res = await TaskJudgeApi.summaries(taskId, ids.slice(at, at + TaskJudgeApi.SUMMARY_BATCH))
      for (const summary of res.summaries) out.set(summary.submissionId, summary)
    } catch {
      // 这一批读不到：那几行画「—」，别的照常。
    }
  }
  return out
}

/** 出题人试跑：排一次，然后读到它跑完。 */
export function useTestTrial() {
  const trial = ref<TaskTestTrial | null>(null)
  const starting = ref(false)
  /** 排不上时后端说的那句话。 */
  const error = ref('')

  const { start, stop } = poller(async () => {
    const current = trial.value
    if (!current) return false
    trial.value = (await TaskJudgeApi.trial(current.runId)).trial
    return inFlight(trial.value)
  }, POLL_MS)

  async function run(spaceId: number, suite: TaskTestSuite, files: TaskTestTrialFile[]) {
    stop()
    error.value = ''
    trial.value = null
    starting.value = true
    try {
      // 试跑不挂在哪个阶段上：阶段 id 不发过去。
      trial.value = (await TaskJudgeApi.createTrial(spaceId, editable(suite), files)).trial
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
      return
    } finally {
      starting.value = false
    }
    if (inFlight(trial.value)) start()
  }

  function clear() {
    stop()
    trial.value = null
    error.value = ''
  }

  return { trial, starting, error, run, stop, clear }
}

/** 页面把试跑放进去，表单里的试跑弹窗就能用（`lib/testTrial.ts`）。`spaceId` 现取：
 *  题目所在的空间，试跑的算力记在它的所有者头上。 */
export function provideTestTrial(spaceId: () => number) {
  const runner = useTestTrial()
  provide(TEST_TRIAL, {
    ...runner,
    run: (suite: TaskTestSuite, files: TaskTestTrialFile[]) => runner.run(spaceId(), suite, files),
  })
  return runner
}

/** 重新评测全部最新提交（只给能批改的人）：不给阶段就是每个挂着测试点的阶段。 */
export function rejudgeLatest(taskId: number, stage?: string | null): Promise<TaskRejudgeCounts> {
  return TaskJudgeApi.rejudge(taskId, stage)
}

/** 学生自测样例：读这一阶段最近一次自测和今天的次数，排一次，还在跑时每隔几秒再读。
 *  这一阶段没有测试点（读出来 404）或读不到时不再读，`view` 留空。 */
export function useSelfTest(ids: { taskId: () => number; participantId: () => number; stage: () => string | null }) {
  const view = ref<TaskSelfTestView | null>(null)
  const starting = ref(false)
  /** 排不上时后端说的那句话（次数用完、过了截止……）。 */
  const error = ref('')

  const { start, stop } = poller(async () => {
    if (!ids.taskId() || !ids.participantId()) return false
    try {
      view.value = await TaskJudgeApi.latestSelfTest(ids.taskId(), ids.participantId(), ids.stage())
    } catch {
      return false
    }
    return inFlight(view.value?.selfTest)
  }, POLL_MS)

  function load() {
    view.value = null
    error.value = ''
    start()
  }

  async function run() {
    if (starting.value) return
    stop()
    error.value = ''
    starting.value = true
    try {
      view.value = await TaskJudgeApi.selfTest(ids.taskId(), ids.participantId(), ids.stage())
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
      return
    } finally {
      starting.value = false
    }
    if (inFlight(view.value?.selfTest)) start()
  }

  return { view, starting, error, load, run, stop }
}
