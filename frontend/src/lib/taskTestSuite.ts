// 发题表单里的测试点：起步的那一套、写出去之前整理成后端收的样子，以及阶段变了以后跟着
// 挪。只做这几件事，不碰界面（和 `lib/taskStages.ts` 同一个分工）。
import type { TaskRubricLine, TaskStage, TaskTestSuite } from '@/types'

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

/** 一套测试点里出题人能改的那几项：读回来才有的版本号、用例数、用例分合计和阶段去掉。 */
export function editable(suite: TaskTestSuite): TaskTestSuite {
  return {
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
      cases: rest.cases.map((entry) => ({
        name: entry.name.trim(),
        points: Number(entry.points) || 0,
        visibility: entry.visibility,
        input: entry.input ?? '',
        expected: entry.expected ?? '',
      })),
    })
  }
  return out
}

/** 有测试点那一行、却没写运行命令或一个用例都没有的第一个阶段的 id；都齐了是 undefined。 */
export function incompleteSuite(
  suites: TaskTestSuite[],
  lines: TaskRubricLine[],
  stages: TaskStage[]
): string | null | undefined {
  for (const line of lines.filter((l) => l.kind === 'tests')) {
    const stage = settledStage(stages, line.stage)
    const suite = suites.find((s) => settledStage(stages, s.stage) === stage)
    if (!suite || !suite.runCmd.trim() || !suite.cases.length) return stage
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
