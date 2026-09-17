import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { answerAgentControl, getAgentControl, getAgentControlResult, sendAgentControl } from '../api'
import { setLocale } from '../i18n'

import AgentControls from './AgentControls.vue'

vi.mock('../api', () => ({
  answerAgentControl: vi.fn(),
  getAgentControl: vi.fn(),
  getAgentControlResult: vi.fn(),
  sendAgentControl: vi.fn(),
}))

beforeEach(() => {
  vi.resetAllMocks()
  setLocale('zh-CN')
  vi.mocked(getAgentControl).mockResolvedValue({ id: 's1', connected: true })
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})
const mount = () =>
  render(AgentControls, {
    props: { topicId: 'topic1', active: true },
    global: { plugins: [createVuetify({ components, directives })] },
  })

it('reports background refusal instead of announcing completion', async () => {
  vi.mocked(sendAgentControl).mockResolvedValue({
    request_id: 'r1',
    status: 'completed',
    result: { response: { subtype: 'success', response: { backgrounded: false } } },
  })
  const view = mount()
  await view.findByText('控制已连接')
  await fireEvent.click(view.getByText('转入后台'))
  await view.findByText('当前任务无法转入后台')
  expect(sendAgentControl).toHaveBeenCalledWith('topic1', 's1', { subtype: 'background_tasks' })
})

it('shows native control errors', async () => {
  vi.mocked(sendAgentControl).mockResolvedValue({
    request_id: 'r1',
    status: 'completed',
    result: { response: { subtype: 'error', error: 'Tool cannot be interrupted' } },
  })
  const view = mount()
  await view.findByText('控制已连接')
  await fireEvent.click(view.getByText('中断当前任务'))
  await view.findByText('Tool cannot be interrupted')
})

it('retains a permission question until the worker acknowledges the answer', async () => {
  vi.mocked(getAgentControl).mockResolvedValue({
    id: 's1',
    connected: true,
    pending: {
      q1: { request_id: 'q1', request: { subtype: 'can_use_tool', tool_name: 'Bash', input: { command: 'pwd' } } },
    },
  })
  vi.mocked(answerAgentControl).mockResolvedValue({ status: 'queued' })
  const view = mount()
  await fireEvent.click(await view.findByText('允许本次'))
  await view.findByText('回答已提交，正在等待会话确认')
  expect(answerAgentControl).toHaveBeenCalledWith('topic1', 's1', 'q1', {
    behavior: 'allow',
    updatedInput: { command: 'pwd' },
  })
  expect(view.getByText('允许本次')).toBeTruthy()
})

it('collects a delayed result without sending the command twice', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.mocked(sendAgentControl).mockResolvedValue({ request_id: 'r1', status: 'queued', result: null })
  vi.mocked(getAgentControlResult).mockResolvedValue({
    status: 'completed',
    result: { response: { subtype: 'success', response: { backgrounded: true } } },
  })
  const view = mount()
  await view.findByText('控制已连接')
  await fireEvent.click(view.getByText('转入后台'))
  await view.findByText('已发送，尚未收到执行结果')
  await vi.advanceTimersByTimeAsync(2100)
  await view.findByText('指令已确认')
  expect(sendAgentControl).toHaveBeenCalledTimes(1)
})
