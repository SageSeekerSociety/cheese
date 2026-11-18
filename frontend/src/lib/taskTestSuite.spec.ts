// 发题表单写出去的测试点：只发有测试点那一行的阶段那一套，阶段写成整理过的 id；缺运行
// 命令或用例时拦住；阶段变了跟着挪。
import type { TaskRubricLine, TaskStage } from '@/types'

import { describe, expect, it } from 'vitest'

import {
  editable,
  incompleteSuite,
  newTestSuite,
  runtimeChanged,
  SCRIPT_RUN_CMD,
  suiteIncomplete,
  suitesAfterStageChange,
  suitesToSend,
  switchKind,
} from './taskTestSuite'

const stages: TaskStage[] = [
  { id: 'a1', name: '一', description: '', dueAt: null },
  { id: 'b2', name: '二', description: '', dueAt: null },
]
const tests = (stage: string | null): TaskRubricLine => ({
  item: '正确性',
  points: 60,
  criteria: '',
  stage,
  kind: 'tests',
})

describe('suitesToSend', () => {
  it('只发有测试点那一行的阶段的那一套，读回来才有的几项去掉', () => {
    const kept = { ...newTestSuite('a1'), revision: 3, caseCount: 1, totalPoints: 10 }
    const dropped = newTestSuite('b2')
    const sent = suitesToSend([kept, dropped], [tests('a1')], stages)
    expect(sent).toHaveLength(1)
    expect(sent[0].stage).toBe('a1')
    expect(sent[0]).not.toHaveProperty('revision')
    expect(sent[0]).not.toHaveProperty('caseCount')
  })

  it('指着已经没有的阶段的那一套归第一个阶段（和评分要点同一条规矩）', () => {
    const sent = suitesToSend([newTestSuite('gone')], [tests('a1')], stages)
    expect(sent.map((s) => s.stage)).toEqual(['a1'])
  })

  it('同一个阶段只发一套', () => {
    const sent = suitesToSend([newTestSuite('a1'), newTestSuite('a1')], [tests('a1')], stages)
    expect(sent).toHaveLength(1)
  })
})

describe('incompleteSuite', () => {
  it('没写运行命令、没有用例或根本没有那一套，都报出那个阶段', () => {
    expect(incompleteSuite([{ ...newTestSuite('a1'), runCmd: ' ' }], [tests('a1')], stages)).toBe('a1')
    expect(incompleteSuite([{ ...newTestSuite('a1'), cases: [] }], [tests('a1')], stages)).toBe('a1')
    expect(incompleteSuite([], [tests('b2')], stages)).toBe('b2')
    expect(incompleteSuite([newTestSuite('a1')], [tests('a1')], stages)).toBeUndefined()
  })
})

describe('suitesAfterStageChange', () => {
  it('删掉一个阶段，它的那一套跟着删；还在的阶段原样', () => {
    const next = suitesAfterStageChange([newTestSuite('a1'), newTestSuite('b2')], ['a1', 'b2'], ['a1'])
    expect(next.map((s) => s.stage)).toEqual(['a1'])
  })

  it('从不分阶段加第一个阶段，原来那一套归它', () => {
    const next = suitesAfterStageChange([newTestSuite(null)], [], ['a1'])
    expect(next.map((s) => s.stage)).toEqual(['a1'])
  })
})

describe('switchKind', () => {
  it('换成测试脚本：共用的几项留着，IO 的用例和自测清掉，运行命令按运行环境填好', () => {
    const io = {
      ...newTestSuite('a1'),
      runtime: 'node22' as const,
      codeDir: 'src',
      showHiddenNames: false,
      selfTestDaily: 5,
      compileCmd: 'tsc',
    }
    const script = switchKind(io, 'SCRIPT')
    expect(script).toMatchObject({
      kind: 'SCRIPT',
      stage: 'a1',
      runtime: 'node22',
      codeDir: 'src',
      showHiddenNames: false,
      memoryMb: 512,
      runCmd: SCRIPT_RUN_CMD.node22,
      compileCmd: '',
      selfTestDaily: null,
      cases: [],
      testFiles: [],
      totalTimeLimitS: 120,
      studentLogLines: 0,
    })
    expect(script.commands).toBeUndefined()
  })

  it('换成命令判分：一条空命令、读 SCORE 行、300 秒；出题人改过的内存不动', () => {
    const command = switchKind({ ...newTestSuite(null), memoryMb: 768 }, 'COMMAND')
    expect(command).toMatchObject({
      kind: 'COMMAND',
      runCmd: '',
      commands: [''],
      scoreSource: 'LINE',
      protectedFiles: [],
      testFiles: [],
      totalTimeLimitS: 300,
      memoryMb: 768,
    })
  })

  it('换回输入/输出用例：命令判分的几项清掉，给一个空样例', () => {
    const back = switchKind({ ...switchKind(newTestSuite(null), 'COMMAND'), commands: ['make grade'] }, 'IO')
    expect(back.kind).toBe('IO')
    expect(back.commands).toBeUndefined()
    expect(back.protectedFiles).toBeUndefined()
    expect(back.memoryMb).toBe(256)
    expect(back.cases).toHaveLength(1)
  })

  it('测试脚本换运行环境：没改过的默认运行命令跟着换，改过的不动', () => {
    const script = switchKind(newTestSuite(null), 'SCRIPT')
    expect(runtimeChanged(script, 'go1.23').runCmd).toBe(SCRIPT_RUN_CMD['go1.23'])
    expect(runtimeChanged({ ...script, runCmd: 'pytest -x' }, 'go1.23').runCmd).toBe('pytest -x')
  })
})

describe('三种形态写出去的样子', () => {
  it('测试脚本逐个配分：每条只带测试名、名称、分值、可见性；文件去掉读回来才有的 bytes', () => {
    const suite = {
      ...switchKind(newTestSuite(null), 'SCRIPT'),
      testFiles: [{ path: ' tests/test_a.py ', content: 'def test_a(): pass', bytes: 18 }],
      cases: [{ testId: ' test_a::test_add ', name: ' 加法 ', points: 30, visibility: 'SAMPLE' as const, input: 'x' }],
    }
    const [sent] = suitesToSend([suite], [tests(null)], [])
    expect(sent.cases).toEqual([{ testId: 'test_a::test_add', name: '加法', points: 30, visibility: 'SAMPLE' }])
    expect(sent.testFiles).toEqual([{ path: 'tests/test_a.py', content: 'def test_a(): pass' }])
    expect(sent).toMatchObject({ kind: 'SCRIPT', totalTimeLimitS: 120, studentLogLines: 0 })
  })

  it('命令判分：空命令去掉，保护文件和判分文件各走各的，二进制的送 base64', () => {
    const suite = {
      ...switchKind(newTestSuite(null), 'COMMAND'),
      commands: [' python3 grade.py ', '  ', 'cat score.txt'],
      scoreSource: 'JSON' as const,
      protectedFiles: [{ path: 'src/config.py', content: 'DEBUG = False\n', bytes: 14 }],
      testFiles: [{ path: 'grader/data.bin', base64: '/wAB', bytes: 3 }],
      studentLogLines: 20,
    }
    const [sent] = suitesToSend([suite], [tests(null)], [])
    expect(sent).toMatchObject({
      kind: 'COMMAND',
      commands: ['python3 grade.py', 'cat score.txt'],
      scoreSource: 'JSON',
      protectedFiles: [{ path: 'src/config.py', content: 'DEBUG = False\n' }],
      testFiles: [{ path: 'grader/data.bin', base64: '/wAB' }],
      studentLogLines: 20,
      totalTimeLimitS: 300,
      cases: [],
    })
    expect(sent.installDeps).toBeUndefined()
  })

  it('IO 的那一份和原来一样，不多带别的形态的字段', () => {
    expect(Object.keys(editable(newTestSuite(null))).sort()).toEqual(
      [
        'kind',
        'runtime',
        'codeDir',
        'compileCmd',
        'runCmd',
        'timeLimitMs',
        'memoryMb',
        'compare',
        'tolerance',
        'showHiddenNames',
        'selfTestDaily',
        'cases',
      ].sort()
    )
  })
})

describe('suiteIncomplete', () => {
  it('测试脚本：要测试文件、运行命令，配分的每条要写测试名', () => {
    const script = switchKind(newTestSuite(null), 'SCRIPT')
    expect(suiteIncomplete(script)).toBe(true)
    const withFile = { ...script, testFiles: [{ path: 'tests/test_a.py', content: '' }] }
    expect(suiteIncomplete(withFile)).toBe(false)
    expect(suiteIncomplete({ ...withFile, runCmd: ' ' })).toBe(true)
    expect(suiteIncomplete({ ...withFile, cases: [{ testId: '', name: '', points: 1, visibility: 'HIDDEN' }] })).toBe(
      true
    )
  })

  it('命令判分：要一条命令，保护文件只打了路径没传原文算没填完', () => {
    const command = switchKind(newTestSuite(null), 'COMMAND')
    expect(suiteIncomplete(command)).toBe(true)
    const ready = { ...command, commands: ['make grade'] }
    expect(suiteIncomplete(ready)).toBe(false)
    expect(suiteIncomplete({ ...ready, protectedFiles: [{ path: 'src/a.py' }] })).toBe(true)
    expect(suiteIncomplete({ ...ready, protectedFiles: [{ path: 'src/a.py', content: '' }] })).toBe(false)
  })
})
