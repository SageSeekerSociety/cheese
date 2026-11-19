// 发题表单里的测试点：起步的那一套、换形态、写出去之前整理成后端收的样子，以及阶段变了
// 以后跟着挪。只做这几件事，不碰界面（和 `lib/taskStages.ts` 同一个分工）。
//
// 三种形态（后端 `test_suite.py`）：输入/输出用例（IO）、测试脚本（SCRIPT）、命令判分
// （COMMAND）。三种共用运行环境、代码目录、内存和隐藏名称的开关；其余各管各的，换形态时
// 别的形态那几项清掉，免得 IO 的用例被当成测试脚本的配分送上去。
import type {
  TaskRubricLine,
  TaskStage,
  TaskTestFile,
  TaskTestRuntime,
  TaskTestSuite,
  TaskTestSuiteKind,
} from '@/types'

import { rowsAfterStageChange, settledStage } from './taskStages'

/** 新加的一套测试点：Python、运行 `python3 main.py`、1 秒 256 MB、忽略行末空白，带一个
 *  空着的样例等出题人填。 */
export function newTestSuite(stage: string | null): TaskTestSuite {
  return {
    stage,
    kind: 'IO',
    runtime: 'python3.12',
    codeDir: '',
    compileCmd: '',
    runCmd: 'python3 main.py',
    timeLimitMs: 1000,
    memoryMb: 256,
    compare: 'TRIM_TRAILING',
    tolerance: 0,
    showHiddenNames: true,
    selfTestDaily: null,
    cases: [{ name: '', points: 10, visibility: 'SAMPLE', input: '', expected: '' }],
  }
}

/** 测试脚本、命令判分整轮时限的默认值（秒）和范围（后端 `SCRIPT_TIME_DEFAULT_S` 等）。 */
export const TOTAL_TIME_DEFAULT_S: Record<Exclude<TaskTestSuiteKind, 'IO'>, number> = { SCRIPT: 120, COMMAND: 300 }
export const MIN_TOTAL_TIME_S = 30
export const MAX_TOTAL_TIME_S = 600
/** 内存默认值（MB）：测试框架自己就要吃几十上百 MB，IO 那个 256 不够。 */
export const MEMORY_DEFAULT_MB: Record<TaskTestSuiteKind, number> = { IO: 256, SCRIPT: 512, COMMAND: 512 }
/** 命令判分最多几条命令；判分程序输出最多给学生看几行（后端 `MAX_COMMANDS` 等）。 */
export const MAX_COMMANDS = 10
export const MAX_STUDENT_LOG_LINES = 200

/** 测试脚本每种运行环境的运行命令（`docs/manual/dev/judge.md` 那张表，镜像里预装的框架）。
 *  C++ 没有预装框架：出题人的测试程序自己把 JUnit XML 写到 `$JUDGE_REPORT`，留空让他填。 */
export const SCRIPT_RUN_CMD: Record<TaskTestRuntime, string> = {
  'python3.12': 'python3 -m pytest -p no:cacheprovider --junitxml="$JUDGE_REPORT" tests',
  node22: 'node --test --test-reporter=junit --test-reporter-destination="$JUDGE_REPORT" tests/',
  java21:
    'javac -d out -cp $JUNIT_JAR $(find . -name \'*.java\'); java -jar $JUNIT_JAR execute -cp out --scan-classpath --reports-dir /tmp/r; cp /tmp/r/TEST-junit-jupiter.xml "$JUDGE_REPORT"',
  'go1.23': 'gotestsum --junitfile "$JUDGE_REPORT" -- ./...',
  cpp17: '',
}

/** 测试脚本换了运行环境：运行命令还是上一个环境的默认值（出题人没改过）就跟着换。 */
export function runtimeChanged(suite: TaskTestSuite, runtime: TaskTestRuntime): TaskTestSuite {
  const next = { ...suite, runtime }
  if (suite.kind === 'SCRIPT' && suite.runCmd.trim() === SCRIPT_RUN_CMD[suite.runtime]) {
    next.runCmd = SCRIPT_RUN_CMD[runtime]
  }
  return next
}

/** 换形态：三种共用的几项（阶段、运行环境、代码目录、隐藏名称的开关）留着；内存还是上一
 *  种形态的默认值就换成这一种的默认值；别的形态那几项清掉，这一种的几项给起步值。自测
 *  只有输入/输出用例能开，换走就关掉。 */
export function switchKind(suite: TaskTestSuite, kind: TaskTestSuiteKind): TaskTestSuite {
  if (suite.kind === kind) return suite
  const fresh = newTestSuite(suite.stage ?? null)
  const memoryMb = suite.memoryMb === MEMORY_DEFAULT_MB[suite.kind] ? MEMORY_DEFAULT_MB[kind] : suite.memoryMb
  const shared = {
    stage: suite.stage,
    runtime: suite.runtime,
    codeDir: suite.codeDir,
    showHiddenNames: suite.showHiddenNames,
    memoryMb,
    revision: suite.revision,
  }
  if (kind === 'IO') return { ...fresh, ...shared }
  const common = {
    ...fresh,
    ...shared,
    kind,
    compileCmd: '',
    selfTestDaily: null,
    cases: [],
    testFiles: [],
    totalTimeLimitS: TOTAL_TIME_DEFAULT_S[kind],
    studentLogLines: 0,
  }
  if (kind === 'SCRIPT') return { ...common, runCmd: SCRIPT_RUN_CMD[suite.runtime] }
  return { ...common, runCmd: '', commands: [''], scoreSource: 'LINE' as const, protectedFiles: [] }
}

/** 一份文件送出去的样子：路径修剪过，`bytes`（读回来才有）去掉，`content`/`base64` 照旧。 */
function fileToSend(file: TaskTestFile): TaskTestFile {
  const out: TaskTestFile = { path: file.path.trim() }
  if (file.base64 != null) out.base64 = file.base64
  else out.content = file.content ?? ''
  return out
}

/** 这一份文件有没有字节：路径打了、原文还没传上来的保护文件没有。 */
export const hasBytes = (file: TaskTestFile) => file.content != null || file.base64 != null

/** 一套测试点里出题人能改的那几项：读回来才有的版本号、用例数、用例分合计和阶段去掉；
 *  测试脚本、命令判分另带它们自己的那几项。 */
export function editable(suite: TaskTestSuite): TaskTestSuite {
  const out: TaskTestSuite = {
    kind: suite.kind,
    runtime: suite.runtime,
    codeDir: suite.codeDir,
    compileCmd: suite.compileCmd,
    runCmd: suite.runCmd,
    timeLimitMs: suite.timeLimitMs,
    memoryMb: suite.memoryMb,
    compare: suite.compare,
    tolerance: suite.tolerance,
    showHiddenNames: suite.showHiddenNames,
    selfTestDaily: suite.selfTestDaily ?? null,
    cases: suite.cases,
  }
  if (suite.kind === 'IO') return out
  out.totalTimeLimitS = suite.totalTimeLimitS ?? TOTAL_TIME_DEFAULT_S[suite.kind]
  out.studentLogLines = Number(suite.studentLogLines) || 0
  out.testFiles = (suite.testFiles ?? []).map(fileToSend)
  if (suite.kind === 'COMMAND') {
    out.commands = suite.commands ?? []
    out.scoreSource = suite.scoreSource ?? 'LINE'
    out.protectedFiles = (suite.protectedFiles ?? []).map(fileToSend)
  }
  return out
}

/** 写出去的测试点：只留有测试点那一行的阶段的那一套（删掉那一行，那一套就不发），
 *  阶段写成整理过的 id，读回来才有的几项去掉。 */
export function suitesToSend(suites: TaskTestSuite[], lines: TaskRubricLine[], stages: TaskStage[]): TaskTestSuite[] {
  const tested = new Set(lines.filter((line) => line.kind === 'tests').map((line) => settledStage(stages, line.stage)))
  const seen = new Set<string | null>()
  const out: TaskTestSuite[] = []
  for (const suite of suites) {
    const stage = settledStage(stages, suite.stage)
    if (!tested.has(stage) || seen.has(stage)) continue
    seen.add(stage)
    const rest = editable(suite)
    out.push({
      ...rest,
      stage,
      runCmd: rest.runCmd.trim(),
      compileCmd: rest.compileCmd.trim(),
      codeDir: rest.codeDir.trim(),
      cases: rest.cases.map((entry) =>
        rest.kind === 'SCRIPT'
          ? {
              testId: (entry.testId ?? '').trim(),
              name: entry.name.trim(),
              points: Number(entry.points) || 0,
              visibility: entry.visibility,
            }
          : {
              name: entry.name.trim(),
              points: Number(entry.points) || 0,
              visibility: entry.visibility,
              input: entry.input ?? '',
              expected: entry.expected ?? '',
            }
      ),
      ...(rest.kind === 'COMMAND'
        ? { commands: (rest.commands ?? []).map((cmd) => cmd.trim()).filter(Boolean), cases: [] }
        : {}),
    })
  }
  return out
}

/** 这一套还缺什么才发得出去：IO 要运行命令和至少一个用例；测试脚本要运行命令、至少一份
 *  测试文件，配了分的每一条都要写测试名；命令判分要至少一条命令，每份文件都有路径，保护
 *  文件都传了原文。 */
export function suiteIncomplete(suite: TaskTestSuite): boolean {
  const filesOk = (files: TaskTestFile[] | undefined) => (files ?? []).every((f) => f.path.trim() && hasBytes(f))
  if (suite.kind === 'SCRIPT') {
    return (
      !suite.runCmd.trim() ||
      !(suite.testFiles ?? []).length ||
      !filesOk(suite.testFiles) ||
      suite.cases.some((entry) => !(entry.testId ?? '').trim())
    )
  }
  if (suite.kind === 'COMMAND') {
    return (
      !(suite.commands ?? []).some((cmd) => cmd.trim()) || !filesOk(suite.testFiles) || !filesOk(suite.protectedFiles)
    )
  }
  return !suite.runCmd.trim() || !suite.cases.length
}

/** 有测试点那一行、那一套却没填完（`suiteIncomplete`）的第一个阶段的 id；都齐了是 undefined。 */
export function incompleteSuite(
  suites: TaskTestSuite[],
  lines: TaskRubricLine[],
  stages: TaskStage[]
): string | null | undefined {
  for (const line of lines.filter((l) => l.kind === 'tests')) {
    const stage = settledStage(stages, line.stage)
    const suite = suites.find((s) => settledStage(stages, s.stage) === stage)
    if (!suite || suiteIncomplete(suite)) return stage
  }
  return undefined
}

/** 阶段列表变了以后测试点跟着挪：和评分要点同一套规矩（`rowsAfterStageChange`）；挪到
 *  同一个阶段上的几套只留第一套。 */
export function suitesAfterStageChange(suites: TaskTestSuite[], oldIds: string[], newIds: string[]): TaskTestSuite[] {
  const { kept } = rowsAfterStageChange(suites, oldIds, newIds)
  const seen = new Set<string | null>()
  return kept.filter((suite) => {
    const stage = suite.stage ?? null
    if (seen.has(stage)) return false
    seen.add(stage)
    return true
  })
}
