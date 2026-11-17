// 题目测试点（方案《题目测试点评分方案》）：出题人在一个阶段上挂的一套输入/输出用例、
// 评测结果和试跑。形状照后端 `app/domain/task/test_suite.py` 与
// `app/api/routes/tasks/judging.py`。

/** 测试点的形态。第一版只做输入/输出用例；测试脚本、命令判分在后面一步。 */
export type TaskTestSuiteKind = 'IO'

/** 五个固定运行环境（`app/domain/judge/runtimes.py`）。 */
export const TASK_TEST_RUNTIMES = ['python3.12', 'cpp17', 'java21', 'node22', 'go1.23'] as const
export type TaskTestRuntime = (typeof TASK_TEST_RUNTIMES)[number]

/** 输出怎么比（`test_suite.COMPARE`）：忽略行末空白（默认）、逐字一致、数值允许误差、忽略大小写。 */
export const TASK_TEST_COMPARES = ['TRIM_TRAILING', 'EXACT', 'NUMERIC', 'IGNORE_CASE'] as const
export type TaskTestCompare = (typeof TASK_TEST_COMPARES)[number]

export type TaskTestVisibility = 'SAMPLE' | 'HIDDEN'

/** 一个用例。学生读到的隐藏用例没有 `input`/`expected`。 */
export interface TaskTestCase {
  name: string
  points: number
  visibility: TaskTestVisibility
  input?: string
  expected?: string
}

/** 一个阶段上的一套测试点。写出去时 `stage` 是阶段的 id（不分阶段是 null）。 */
export interface TaskTestSuite {
  stage?: string | null
  kind: TaskTestSuiteKind
  runtime: TaskTestRuntime
  codeDir: string
  compileCmd: string
  runCmd: string
  timeLimitMs: number
  memoryMb: number
  compare: TaskTestCompare
  tolerance: number
  showHiddenNames: boolean
  selfTestDaily?: number | null
  cases: TaskTestCase[]
  /** 读回来才有：第几版、几个用例、用例分合计。 */
  revision?: number
  caseCount?: number
  totalPoints?: number
}

/** 一个用例的评测状态（`app/domain/judge/runner.py`）。`ok` 是通过。 */
export type TaskJudgeCaseStatus =
  | 'ok'
  | 'wrong'
  | 'timeout'
  | 'memory'
  | 'runtime_error'
  | 'output_limit'
  | 'compile_error'

export interface TaskJudgeCase {
  name: string
  visibility: TaskTestVisibility
  status: TaskJudgeCaseStatus | string
  points: number
  ms?: number | null
  memoryKb?: number | null
  /** 只给能批改的人：用例 id、隐藏用例的输入/期望/实际输出。 */
  caseId?: number | null
  input?: string | null
  expected?: string | null
  actual?: string | null
  stderr?: string | null
}

export type TaskJudgeState = 'queued' | 'running' | 'done' | 'failed'

/** 一次评测（`judge_queue.run_to_api`）。还没跑完时 `cases` 为空。 */
export interface TaskJudgeRun {
  state: TaskJudgeState
  purpose: string
  codeSha: string
  suiteRevision: number
  passedPoints: number
  totalPoints: number
  createdAt: number
  startedAt: number | null
  finishedAt: number | null
  machineMs: number | null
  /** 失败原因：后端文案的 key 和参数（`NoticeText.descriptor`）。 */
  error?: { key: string; params?: Record<string, unknown> } | null
  compileOutput?: string | null
  cases?: TaskJudgeCase[]
  passedCases?: number
  caseCount?: number
}

/** 一次试跑：评测结果，加最慢的用例用了多久、时限多少。 */
export interface TaskTestTrial extends TaskJudgeRun {
  runId: number
  timeLimitMs: number
  slowestMs?: number | null
}

/** 提交页那块「将要评测的代码」：默认分支现在的 commit。 */
export interface TaskCodeVersion {
  sha: string
  branch: string
  subject: string
  /** 毫秒；读不出来是 null。 */
  pushedAt: number | null
}

/** 参与者列表「测试点」一列：一版提交最近一次评测的摘要。 */
export interface TaskJudgeSummary {
  submissionId: number
  state: TaskJudgeState
  passedCases: number
  caseCount: number
  passedPoints: number
  totalPoints: number
}

/** 试跑时带上的参考代码：一份文件的路径和文本。 */
export interface TaskTestTrialFile {
  path: string
  content: string
}
