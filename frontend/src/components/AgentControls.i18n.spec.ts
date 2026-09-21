import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { getAgentControl } from '../api'
import i18n, { setLocale } from '../i18n'

import AgentControls from './AgentControls.vue'

vi.mock('../api', () => ({
  answerAgentControl: vi.fn(),
  getAgentControl: vi.fn(),
  getAgentControlResult: vi.fn(),
  sendAgentControl: vi.fn(),
}))

const CJK = /[㐀-䶿一-鿿豈-﫿]/

// 控制条本身要等 getAgentControl 回来才有「已连接」，所以它是个真数据标记；
// 展开后的任务行、提问都带数据，可以据它们等渲染稳定。
const state = {
  id: 's1',
  connected: true,
  tasks: {
    t1: { task_id: 't1', description: 'build', status: 'running', tool_use_id: 'tool1' },
    t2: { task_id: 't2', description: 'deploy', status: 'queued' },
    t3: { task_id: 't3', description: 'scan', status: 'weird' },
    t4: { task_id: 't4', subtype: 'task_progress' },
  },
  pending: {
    q1: { request_id: 'q1', request: { subtype: 'can_use_tool', tool_name: 'Bash', input: { command: 'pwd' } } },
    q2: { request_id: 'q2', request: { subtype: 'can_use_tool', input: { command: 'ls' } } },
    q3: {
      request_id: 'q3',
      request: {
        subtype: 'ask_user_question',
        input: {
          questions: [
            { question: 'Which color?', options: [{ label: 'Red', description: 'warm' }, { label: 'Blue' }] },
          ],
        },
      },
    },
  },
}

beforeEach(() => {
  vi.resetAllMocks()
  setLocale('zh-CN')
  vi.mocked(getAgentControl).mockResolvedValue(state)
})
afterEach(() => cleanup())

const text = (element: Element) => (element.textContent ?? '').replace(/\s+/g, ' ')

async function mountControls(...markers: string[]) {
  const view = render(AgentControls, {
    props: { topicId: 'topic1', active: true },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
  await vi.waitFor(() => {
    const page = text(view.container)
    for (const marker of markers) expect(page, marker).toContain(marker)
  })
  return { view, page: () => text(view.container) }
}

async function expand(view: ReturnType<typeof render>, label: string, running: string) {
  await fireEvent.click(view.getByText(label))
  await vi.waitFor(() => expect(text(view.container), '任务行').toContain(running))
}

it('讲中文：控制条、任务状态、提问和操作表单都是中文', async () => {
  const { view, page } = await mountControls('控制已连接')
  expect(page()).toContain('转入后台')
  expect(page()).toContain('中断当前任务')
  expect(page()).toContain('Bash 请求执行许可')
  expect(page()).toContain('操作 请求执行许可')
  expect(page()).toContain('你的回答')
  expect(page()).toContain('Red：warm')
  expect(page()).toContain('提交回答')
  expect(page()).toContain('允许本次')
  expect(page()).toContain('拒绝')

  await expand(view, '更多控制', 'build · 运行中')
  const expanded = page()
  expect(expanded).toContain('收起控制')
  expect(expanded).toContain('deploy · 排队中')
  expect(expanded).toContain('scan · 状态待更新')
  expect(expanded).toContain('t4 · 运行中')
  expect(expanded).toContain('会话状态')
  expect(expanded).toContain('停止')
})

it('整个控件在英文下不留一个汉字', async () => {
  setLocale('en')
  const { view, page } = await mountControls('Control connected')
  expect(page()).toContain('Move to background')
  expect(page()).toContain('Interrupt current task')
  expect(page()).toContain('Bash asks for permission')
  expect(page()).toContain('Operation asks for permission')
  expect(page()).toContain('Your answer')
  expect(page()).toContain('Red: warm')
  expect(page()).toContain('Submit answer')
  expect(page()).toContain('Allow once')
  expect(page()).toContain('Deny')

  await expand(view, 'More controls', 'build · Running')
  const expanded = page()
  expect(expanded).toContain('Fewer controls')
  expect(expanded).toContain('deploy · Queued')
  expect(expanded).toContain('scan · Status pending')
  expect(expanded).toContain('t4 · Running')
  expect(expanded).toContain('Session state')
  expect(expanded).toContain('Stop')
  expect(CJK.test(expanded), expanded).toBe(false)
})

it('把界面切成英文后，已经画出来的状态和选项立刻跟着换', async () => {
  const { view, page } = await mountControls('控制已连接')
  await expand(view, '更多控制', 'build · 运行中')
  expect(page()).toContain('会话状态')

  setLocale('en')
  await vi.waitFor(() => expect(page(), '任务状态').toContain('build · Running'))
  const switched = page()
  expect(switched).toContain('deploy · Queued')
  expect(switched).toContain('scan · Status pending')
  expect(switched).toContain('t4 · Running')
  expect(switched).toContain('Fewer controls')
  expect(switched).toContain('Session state')
  expect(switched).toContain('Stop')
  expect(switched).toContain('Bash asks for permission')
  expect(CJK.test(switched), switched).toBe(false)
})
