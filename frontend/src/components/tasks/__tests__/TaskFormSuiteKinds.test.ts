// 测试点的三种形态：换形态时别的形态那几项清掉、换回来原来填的还在；测试脚本逐个配分；
// 命令判分的命令列表和保护文件（打路径、传原文）；输入/输出用例从 zip 导入。
import type { TaskRubricLine, TaskSubmissionSchemaEntry, TaskTestSuite } from '@/types'

import { defineComponent, h, ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest'

import TaskFormGrading from '../form/TaskFormGrading.vue'

import i18n, { setLocale } from '@/i18n'
import { newTestSuite, switchKind } from '@/lib/taskTestSuite'
import { zipFixture } from '@/lib/zipFixture.spec.helper'

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  if (!globalThis.visualViewport) {
    Object.defineProperty(globalThis, 'visualViewport', {
      configurable: true,
      value: { width: 1024, height: 768, offsetLeft: 0, offsetTop: 0, addEventListener() {}, removeEventListener() {} },
    })
  }
})

beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

function mount(initial: TaskRubricLine[] = [], suites: TaskTestSuite[] = []) {
  const rubric = ref<TaskRubricLine[]>([...initial])
  const testSuite = ref<TaskTestSuite[]>([...suites])
  const items = ref<TaskSubmissionSchemaEntry[]>([])
  const Host = defineComponent({
    setup: () => () =>
      h(components.VApp, null, () =>
        h(TaskFormGrading, {
          rubric: rubric.value,
          'onUpdate:rubric': (next: TaskRubricLine[]) => (rubric.value = next),
          testSuite: testSuite.value,
          'onUpdate:testSuite': (next: TaskTestSuite[]) => (testSuite.value = next),
          submissionSchema: items.value,
          'onUpdate:submissionSchema': (next: TaskSubmissionSchemaEntry[]) => (items.value = next),
          rubricPublic: true,
          'onUpdate:rubricPublic': () => undefined,
        })
      ),
  })
  render(Host, { global: { plugins: [createVuetify({ components, directives }), i18n] } })
  return { rubric, testSuite }
}

async function openAddMenu() {
  await fireEvent.click(screen.getByTestId('task-form-add-line'))
  await screen.findByTestId('task-form-add-tests')
}

const testsLine = (points = 60): TaskRubricLine => ({
  item: '正确性',
  points,
  criteria: '',
  stage: null,
  kind: 'tests',
})

const kindButton = (name: string) => screen.getByRole('radio', { name })
const inputOf = (testid: string) => screen.getByTestId(testid).querySelector('input') as HTMLInputElement

async function pickFiles(testid: string, files: File[]) {
  const input = screen.getByTestId(testid) as HTMLInputElement
  Object.defineProperty(input, 'files', { configurable: true, value: files })
  await fireEvent.change(input)
}

describe('测试点的形态', () => {
  it('换成测试脚本清掉 IO 的用例；同一次编辑里换回来，原来填的用例还在', async () => {
    const io = {
      ...newTestSuite(null),
      cases: [{ name: '加法', points: 10, visibility: 'SAMPLE' as const, input: '1 2', expected: '3' }],
    }
    const { testSuite } = mount([testsLine()], [io])

    await fireEvent.click(kindButton('测试脚本'))
    await waitFor(() => expect(testSuite.value[0].kind).toBe('SCRIPT'))
    expect(testSuite.value[0].cases).toEqual([])
    expect(testSuite.value[0].runCmd).toContain('$JUDGE_REPORT')
    expect(screen.getByTestId('task-form-script-warning')).toBeTruthy()

    await fireEvent.click(kindButton('输入/输出用例'))
    await waitFor(() => expect(testSuite.value[0].kind).toBe('IO'))
    expect(testSuite.value[0].cases).toEqual(io.cases)
  })

  it('测试脚本：上传测试文件，切到逐个配分写测试名和分值', async () => {
    const { testSuite } = mount([testsLine()], [switchKind(newTestSuite(null), 'SCRIPT')])

    await pickFiles('task-form-script-files-input', [new File(['def test_add(): pass\n'], 'test_add.py')])
    await waitFor(() => expect(testSuite.value[0].testFiles).toHaveLength(1))
    expect(testSuite.value[0].testFiles![0]).toMatchObject({ path: 'test_add.py', content: 'def test_add(): pass\n' })

    await fireEvent.click(kindButton('逐个测试配分'))
    await waitFor(() => expect(testSuite.value[0].cases).toHaveLength(1))
    await fireEvent.update(inputOf('task-form-script-test-0'), 'test_add::test_add')
    await fireEvent.update(inputOf('task-form-script-points-0'), '40')
    await waitFor(() => expect(testSuite.value[0].cases[0]).toMatchObject({ testId: 'test_add::test_add', points: 40 }))

    await fireEvent.click(screen.getByTestId('task-form-script-add-test'))
    await waitFor(() => expect(testSuite.value[0].cases).toHaveLength(2))

    // 换回「一样重」：配分行清掉。
    await fireEvent.click(kindButton('每个测试一样重'))
    await waitFor(() => expect(testSuite.value[0].cases).toEqual([]))
  })

  it('命令判分：加命令、调顺序、删命令，写回那一套', async () => {
    const { testSuite } = mount([testsLine()], [switchKind(newTestSuite(null), 'COMMAND')])

    await fireEvent.update(inputOf('task-form-command-0'), 'make build')
    await waitFor(() => expect(testSuite.value[0].commands).toEqual(['make build']))
    await fireEvent.click(screen.getByTestId('task-form-command-add'))
    await waitFor(() => expect(testSuite.value[0].commands).toEqual(['make build', '']))
    await fireEvent.update(inputOf('task-form-command-1'), 'python3 grade.py')
    await fireEvent.click(screen.getByTestId('task-form-command-up-1'))
    await waitFor(() => expect(testSuite.value[0].commands).toEqual(['python3 grade.py', 'make build']))
    await fireEvent.click(screen.getByTestId('task-form-command-remove-1'))
    await waitFor(() => expect(testSuite.value[0].commands).toEqual(['python3 grade.py']))

    await fireEvent.click(kindButton('score.json'))
    await waitFor(() => expect(testSuite.value[0].scoreSource).toBe('JSON'))
  })

  it('命令判分的保护文件：先打路径（标「还没传原文」），再给这一行传原文', async () => {
    const { testSuite } = mount([testsLine()], [switchKind(newTestSuite(null), 'COMMAND')])

    await fireEvent.click(screen.getByTestId('task-form-command-protected-add'))
    await waitFor(() => expect(testSuite.value[0].protectedFiles).toEqual([{ path: '' }]))
    await fireEvent.update(inputOf('task-form-command-protected-path-0'), 'src/config.py')
    await waitFor(() => expect(testSuite.value[0].protectedFiles).toEqual([{ path: 'src/config.py' }]))
    expect(screen.getByTestId('task-form-command-protected-missing-0')).toBeTruthy()

    await fireEvent.click(screen.getByTestId('task-form-command-protected-upload-0'))
    await pickFiles('task-form-command-protected-input', [new File(['DEBUG = False\n'], 'config.py')])
    await waitFor(() =>
      expect(testSuite.value[0].protectedFiles).toEqual([
        { path: 'src/config.py', content: 'DEBUG = False\n', bytes: 14 },
      ])
    )
    expect(screen.queryByTestId('task-form-command-protected-missing-0')).toBeNull()

    await pickFiles('task-form-command-graders-input', [new File(['print("SCORE: 1/1")'], 'grade.py')])
    await waitFor(() => expect(testSuite.value[0].testFiles).toMatchObject([{ path: 'grade.py' }]))
  })

  it('导入用例：zip 里成对的文件换掉起步的空样例，追加成隐藏用例；超过 64 KB 的整包不导入', async () => {
    const { testSuite } = mount([testsLine()], [newTestSuite(null)])
    const good = await zipFixture([
      { name: '1.in', data: '1 2\n' },
      { name: '1.out', data: '3\n' },
      { name: '2.in', data: '2 2\n', method: 8 },
      { name: '2.ans', data: '4\n' },
    ])
    await pickFiles('task-form-tests-import-input', [new File([good], 'cases.zip')])
    await waitFor(() => expect(testSuite.value[0].cases).toHaveLength(2))
    expect(testSuite.value[0].cases[1]).toEqual({
      name: '2',
      points: 10,
      visibility: 'HIDDEN',
      input: '2 2\n',
      expected: '4\n',
    })
    expect(screen.getByTestId('task-form-tests-import-note').textContent).toContain('导入了 2 个隐藏用例')

    const big = await zipFixture([
      { name: '3.in', data: 'x'.repeat(64 * 1024 + 1), method: 8 },
      { name: '3.out', data: 'y' },
    ])
    await pickFiles('task-form-tests-import-input', [new File([big], 'big.zip')])
    await waitFor(() => expect(screen.getByTestId('task-form-tests-import-note').textContent).toContain('超过 64 KB'))
    expect(testSuite.value[0].cases).toHaveLength(2)
  })
})
