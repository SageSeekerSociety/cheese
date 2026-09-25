import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { getAgentControl, sendAgentControl } from '../api'

import AgentControls from './AgentControls.vue'

vi.mock('../api', () => ({
  getAgentControl: vi.fn(),
  sendAgentControl: vi.fn(),
}))

beforeEach(() => {
  vi.resetAllMocks()
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

it("lists the session's commands beside its agents and stops one", async () => {
  vi.mocked(getAgentControl).mockResolvedValue({
    id: 's1',
    connected: true,
    tasks: {
      b7k2m9x4q: {
        task_id: 'b7k2m9x4q',
        status: 'running',
        task_type: 'local_bash',
      },
      a1: { task_id: 'a1', description: '派分身去查', status: 'completed', task_type: 'local_agent' },
    },
  })
  vi.mocked(sendAgentControl).mockResolvedValue({
    request_id: 'r1',
    status: 'completed',
    result: { response: { subtype: 'success', response: { status: 'stopped' } } },
  })
  const view = mount()
  await view.findByText('控制已连接')
  await fireEvent.click(view.getByText('更多控制'))
  await view.findByText('b7k2m9x4q · 运行中')
  expect(view.getByText('派分身去查 · 已完成')).toBeTruthy()
  const stops = view.getAllByText('停止')
  await fireEvent.click(stops[0])
  await view.findByText('指令已确认')
  expect(sendAgentControl).toHaveBeenCalledWith('topic1', 's1', {
    subtype: 'stop_task',
    task_id: 'b7k2m9x4q',
  })
})

it('has no colour control and no question panel', async () => {
  const view = mount()
  await view.findByText('控制已连接')
  await fireEvent.click(view.getByText('更多控制'))
  expect(view.queryByText('会话颜色')).toBeNull()
  expect(view.queryByText('允许本次')).toBeNull()
})

it('takes the room session state off the socket instead of asking again', async () => {
  // The room already holds a socket, so a task starting arrives as a frame.
  // What this pins is the half that saves the requests: having been given one,
  // the panel does not go back to asking every two seconds.
  vi.useFakeTimers({ shouldAdvanceTime: true })
  const props = { topicId: 'topic1', active: true }
  const view = render(AgentControls, {
    props: { ...props, pushed: null },
    global: { plugins: [createVuetify({ components, directives })] },
  })
  await view.rerender({
    ...props,
    pushed: {
      id: 's2',
      connected: true,
      tasks: { t1: { task_id: 't1', description: '长命令', status: 'running', task_type: 'local_bash' } },
    },
  })
  await fireEvent.click(view.getByText('更多控制'))
  await view.findByText('长命令 · 运行中')
  await vi.advanceTimersByTimeAsync(6000)
  expect(getAgentControl).toHaveBeenCalledTimes(1)
})
