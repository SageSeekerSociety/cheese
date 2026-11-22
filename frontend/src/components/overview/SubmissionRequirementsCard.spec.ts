// 提交要求卡：文本项去提交表单，代码项（题目挂了测试点）去新建任务，卡底显示最近一版和测试结果。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import SubmissionRequirementsCard from './SubmissionRequirementsCard.vue'

import i18n, { setLocale } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

function mount(props: Record<string, unknown>) {
  const onGo = vi.fn()
  const onNew = vi.fn()
  const view = render(SubmissionRequirementsCard, {
    props: {
      entries: [],
      hasTests: false,
      submittable: true,
      latest: null,
      ...props,
      onGoSubmit: onGo,
      onNewTask: onNew,
    },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
  return { ...view, onGo, onNew }
}

it('text item and the footer both go to the submit form', async () => {
  const { onGo } = mount({ entries: [{ prompt: 'SQL 答案', type: 'TEXT' }] })
  expect(screen.getByText('SQL 答案')).toBeTruthy()
  await fireEvent.click(screen.getByTestId('submission-row-go'))
  await fireEvent.click(screen.getByTestId('submission-go'))
  expect(onGo).toHaveBeenCalledTimes(2)
  expect(screen.queryByTestId('submission-new-task')).toBeNull()
})

it('a task with test cases offers a new task for code, even when no file item is listed', async () => {
  const { onNew } = mount({ entries: [{ prompt: '说明', type: 'TEXT' }], hasTests: true })
  await fireEvent.click(screen.getByTestId('submission-new-task'))
  expect(onNew).toHaveBeenCalledTimes(1)
  expect(screen.getAllByTestId('submission-row-code')).toHaveLength(1)
})

it('a file item with test cases is the code row, not a second one', () => {
  mount({ entries: [{ prompt: 'main.cpp', type: 'FILE' }], hasTests: true })
  expect(screen.getAllByTestId('submission-row-code')).toHaveLength(1)
})

it('shows the latest version and its test result; says so when nothing was handed in', () => {
  const view = mount({
    latest: { version: 2, createdAt: Date.now(), tests: { state: 'done', passed: 3, total: 5 } },
    hasTests: true,
  })
  expect(screen.getByTestId('submission-latest').textContent).toContain('第 2 版')
  expect(screen.getByTestId('submission-tests').textContent).toContain('3/5')
  view.unmount()
  mount({ entries: [{ prompt: 'a', type: 'TEXT' }] })
  expect(screen.getByTestId('submission-latest').textContent).toContain('还没有交过')
})

it('closed tasks disable the submit buttons', () => {
  mount({ entries: [{ prompt: 'a', type: 'TEXT' }], submittable: false })
  expect((screen.getByTestId('submission-go') as HTMLButtonElement).disabled).toBe(true)
})
