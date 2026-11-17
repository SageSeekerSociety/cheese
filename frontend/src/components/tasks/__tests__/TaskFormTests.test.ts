// 「提交与评分」里测试点那一行：加这一行时这个阶段配上一套起步的用例，一个阶段只能有一行，
// 和额度用量各算各的；改设置、加用例都写回那一套；删这一行连那一套一起删。
import type { TaskRubricLine, TaskSubmissionSchemaEntry, TaskTestSuite } from '@/types'

import { defineComponent, h, ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest'

import TaskFormGrading from '../form/TaskFormGrading.vue'

import i18n, { setLocale } from '@/i18n'
import { newTestSuite } from '@/lib/taskTestSuite'

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

describe('测试点那一行', () => {
  it('从「添加一项」加进来时配上一套起步的用例，一个阶段只能加一行', async () => {
    const { rubric, testSuite } = mount()
    await openAddMenu()
    await fireEvent.click(screen.getByTestId('task-form-add-tests'))

    await waitFor(() => expect(rubric.value).toHaveLength(1))
    expect(rubric.value[0]).toMatchObject({ kind: 'tests', item: '正确性' })
    expect(testSuite.value).toHaveLength(1)
    expect(testSuite.value[0]).toMatchObject({ kind: 'IO', runCmd: 'python3 main.py', stage: null })
    expect(testSuite.value[0].cases).toHaveLength(1)

    await openAddMenu()
    expect(screen.getByTestId('task-form-add-tests').classList.contains('v-list-item--disabled')).toBe(true)
    await fireEvent.click(screen.getByTestId('task-form-add-tests'))
    expect(rubric.value).toHaveLength(1)
    expect(testSuite.value).toHaveLength(1)
  })

  it('有了测试点那一行，额度用量照样能加：两种自动行各算各的', async () => {
    mount([testsLine()], [newTestSuite(null)])
    await openAddMenu()
    expect(screen.getByTestId('task-form-add-usage').classList.contains('v-list-item--disabled')).toBe(false)
  })

  it('加一个用例、改运行命令，都写回那一套', async () => {
    const { testSuite } = mount([testsLine()], [newTestSuite(null)])

    await fireEvent.click(screen.getByTestId('task-form-tests-add-case'))
    await waitFor(() => expect(testSuite.value[0].cases).toHaveLength(2))
    // 第二个起默认是隐藏用例：第一个是给学生看的样例。
    expect(testSuite.value[0].cases[1].visibility).toBe('HIDDEN')

    const run = screen.getByTestId('task-form-tests-run').querySelector('input') as HTMLInputElement
    await fireEvent.update(run, 'python3 solve.py')
    await waitFor(() => expect(testSuite.value[0].runCmd).toBe('python3 solve.py'))

    const input = screen.getByTestId('task-form-tests-input-1').querySelector('textarea') as HTMLTextAreaElement
    await fireEvent.update(input, '1 2\n')
    await waitFor(() => expect(testSuite.value[0].cases[1].input).toBe('1 2\n'))
  })

  it('删掉这一行，这个阶段的那一套用例一起删', async () => {
    const { rubric, testSuite } = mount([testsLine()], [newTestSuite(null)])
    await fireEvent.click(screen.getByRole('button', { name: '删掉测试点这一项和它的用例' }))
    await waitFor(() => expect(rubric.value).toHaveLength(0))
    expect(testSuite.value).toHaveLength(0)
  })

  it('页面没有放试跑时（组件预览、一次发好几道）不显示「试跑」', () => {
    mount([testsLine()], [newTestSuite(null)])
    expect(screen.queryByTestId('task-form-tests-trial')).toBeNull()
  })
})
