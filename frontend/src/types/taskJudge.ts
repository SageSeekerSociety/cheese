// 题目测试点（方案《题目测试点评分方案》）：出题人在一个阶段上挂的一套输入/输出用例、
// 评测结果和试跑。形状照后端 `app/domain/task/test_suite.py` 与
// `app/api/routes/tasks/judging.py`。

/** 测试点的形态：输入/输出用例、测试脚本（出题人的测试文件 + JUnit XML 报告）、命令判分
 *  （按顺序跑几条命令，读 `SCORE: x/y` 或 score.json）。 */
export const TASK_TEST_KINDS = ['IO', 'SCRIPT', 'COMMAND'] as const
export type TaskTestSuiteKind = (typeof TASK_TEST_KINDS)[number]

/** 命令判分的分从哪读：最后一行 `SCORE: x/y`，或仓库根的 score.json。 */
export type TaskTestScoreSource = 'LINE' | 'JSON'

/** 测试脚本的测试文件、命令判分的判分文件与保护文件：相对路径加字节。能按 UTF-8 读的
 *  给 `content`，读不了的给 `base64`；读回来另带 `bytes`。送回读到的那一份就是「没改」。 */
export interface TaskTestFile {
  path: string
  content?: string
  base64?: string
  bytes?: number
}

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
  /** 测试脚本：这一条给分的测试叫什么（`classname::name` 或只写 `name`）。 */
  testId?: string
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
  /** 测试脚本、命令判分：整轮时限（秒，30–600）。 */
  totalTimeLimitS?: number | null
  /** 测试脚本、命令判分：判分失败时学生能看程序输出的前几行（0 = 不给看）。 */
  studentLogLines?: number
  /** 装依赖要联网，这一步后端一律拒；表单不提供，读回来总是 false。 */
  installDeps?: boolean
  /** 测试脚本的测试文件、命令判分的判分文件（只有出题人读得到）。 */
  testFiles?: TaskTestFile[]
  /** 命令判分：按顺序跑的命令（只有出题人读得到）。 */
  commands?: string[]
  scoreSource?: TaskTestScoreSource
  /** 命令判分：每次评测前用出题人的原版覆盖的文件（只有出题人读得到字节）。 */
  protectedFiles?: TaskTestFile[]
  /** 读回来才有：保护文件的路径（学生也读得到）、测试文件几份。 */
  protectedPaths?: string[]
  testFileCount?: number
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
  /** 测试脚本：报告里的测试名与失败信息（学生只看得到样例那几条的信息）。 */
  testId?: string | null
  message?: string | null
  /** 命令判分：这一项的得分与满分（可以是小数）。 */
  score?: number | null
  maxScore?: number | null
  /** 测试脚本：被跳过（算没过）、报告里有但出题人没配分。 */
  skipped?: boolean | null
  unscored?: boolean | null
  /** 命令判分：score.json 里 `visible: false` 的那一项，只有老师读得到。 */
  private?: boolean | null
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
  /** 出题人允许学生看的程序输出（前 `studentLogLines` 行）。 */
  studentOutput?: string | null
  /** 判分失败：跑完了但报告或分数读不出来，两个分数不算数，老师手动给分。 */
  scoreFailed?: boolean
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

/** 学生自测：最近一次（没自测过是 null）和今天的次数。`dailyLimit` 是出题人给的每天
 *  次数，`resetsAt`（毫秒）是下一次清零。 */
export interface TaskSelfTestView {
  selfTest: TaskJudgeRun | null
  dailyLimit: number | null
  usedToday: number
  resetsAt: number
}

/** 重新评测排了多少：新排上的、直接复用的、已经在这一版上的、跳过的。 */
export interface TaskRejudgeCounts {
  queued: number
  reused: number
  unchanged: number
  skipped: number
}

/** 一个用例在最新那几版提交里过了几份（`judge_stats`）。`rate` 是 0–1。 */
export interface TaskJudgeCaseRate {
  name: string
  visibility: TaskTestVisibility | string
  judged: number
  passed: number
  rate: number | null
  /** 没过的那几份里最常见的状态和份数；全过了是 null。 */
  topFailure: { status: TaskJudgeCaseStatus | string; count: number } | null
}

/** 一个阶段的测试点数据：每个参与者在这一阶段最新那一版提交的最近一次评测。 */
export interface TaskJudgeStageStats {
  kind: TaskTestSuiteKind | string
  submissions: number
  /** 跑完、读得出分的份数；`buckets` 是它们的测试分按 0–9% … 90–100% 分的十档。 */
  judged: number
  scoreFailed: number
  failed: number
  inFlight: number
  notJudged: number
  /** 算进来、却是按旧版测试评的份数（改过测试点还没重评）。 */
  outdated: number
  averageRate: number | null
  buckets: number[]
  /** 最差的在前。 */
  cases: TaskJudgeCaseRate[]
}

/** 这道题评测一共花的额度：学生提交的评测（含重评）与自测；出题人的试跑不在内。 */
export interface TaskJudgeCredits {
  total: number
  submissions: number
  selfTests: number
  machineMs: number
  chargedRuns: number
}

/** `GET /tasks/{id}/judge/stats`。 */
export interface TaskJudgeStatsView {
  /** 挂着测试点的阶段 id；不分阶段的题是 `[null]`，没有测试点是空表。 */
  stages: (string | null)[]
  stage: string | null
  stats: TaskJudgeStageStats | null
  credits: TaskJudgeCredits
}
