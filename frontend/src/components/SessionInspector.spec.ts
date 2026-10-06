// 现场 only watches its session: a person can look at the session's state and
// ask it read-only questions, and there is nothing here that changes anything.
import type { Component, PropType } from 'vue'
import type { AgentControlState } from '../cx_types'

import { defineComponent, h } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, within } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, expect, it, vi } from 'vitest'

import { getAgentControl, getRoomMcpServers, sendAgentControl } from '../api'
import { useSessionInspector } from '../composables/useSessionInspector'

import SessionInspector from './SessionInspector.vue'

import i18n, { setLocale } from '@/i18n'

vi.mock('../api', () => ({
  getAgentControl: vi.fn(),
  getRoomMcpServers: vi.fn(),
  sendAgentControl: vi.fn(),
}))

beforeAll(() => setLocale('zh-CN'))
beforeEach(() => {
  vi.resetAllMocks()
  // Vuetify places a select's menu against the viewport, which jsdom lacks.
  vi.stubGlobal('visualViewport', new EventTarget())
  vi.stubGlobal('devicePixelRatio', 1)
  vi.mocked(getAgentControl).mockResolvedValue({ id: 's1', connected: true })
  vi.mocked(getRoomMcpServers).mockResolvedValue({ servers: [] })
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})
// jsdom does not submit a form from its submit button's click; a browser does.
const ask = (view: ReturnType<typeof render>) =>
  fireEvent.submit(view.getByRole('button', { name: '查看' }).closest('form')!)
// 这一只只吃 props：轮询、上一帧会话状态、把那一句问出去都在
// `useSessionInspector` 里，由渲染它的那一层调一次、整包递下来。测试站在那一层的位置上。
const Host = defineComponent({
  props: {
    topicId: { type: String, required: true },
    active: { type: Boolean, default: false },
    // 不传 = 没有推的那条路（快的那档轮询）；传 null = 有那条路但还没有帧。
    pushed: { type: Object as PropType<AgentControlState | null>, default: undefined },
  },
  setup(props) {
    const session = useSessionInspector({
      topicId: () => props.topicId,
      active: () => props.active,
      pushed: () => props.pushed,
    })
    return () => h(SessionInspector as unknown as Component, { session })
  },
})
const mount = () =>
  render(Host, {
    props: { topicId: 'topic1', active: true },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })

it("lists the session's tasks without offering to move or stop them", async () => {
  vi.mocked(getAgentControl).mockResolvedValue({
    id: 's1',
    connected: true,
    tasks: {
      b7k2m9x4q: { task_id: 'b7k2m9x4q', status: 'running', task_type: 'local_bash', tool_use_id: 'toolu_1' },
      a1: { task_id: 'a1', description: '派分身去查', status: 'completed', task_type: 'local_agent' },
    },
  })
  const view = mount()
  await view.findByText('会话已连接')
  await fireEvent.click(view.getByText('查看详情'))
  await view.findByText('b7k2m9x4q · 运行中')
  expect(view.getByText('派分身去查 · 已完成')).toBeTruthy()
  const buttons = view.getAllByRole('button').map((button) => button.textContent?.trim())
  expect(buttons).toEqual(['收起详情', '查看'])
  for (const gone of ['转入后台', '中断当前任务', '停止', '执行', '打开授权页面']) {
    expect(view.queryByText(gone)).toBeNull()
  }
})

it('offers only questions that read the session', async () => {
  const view = mount()
  await view.findByText('会话已连接')
  await fireEvent.click(view.getByText('查看详情'))
  await fireEvent.mouseDown(view.getByRole('combobox'))
  const menu = await view.findByRole('listbox')
  const offered = within(menu)
    .getAllByRole('option')
    .map((option) => option.textContent?.trim())
  expect(offered).toEqual(['会话状态', '查找文件', '查看文件', '工作区变更', '上下文用量', '账号用量', '外部工具连接'])
})

it('shows what the session answers', async () => {
  vi.mocked(sendAgentControl).mockResolvedValue({
    request_id: 'r1',
    status: 'completed',
    result: { response: { subtype: 'success', response: { model: 'claude-x', mcpServers: [] } } },
  })
  const view = mount()
  await view.findByText('会话已连接')
  await fireEvent.click(view.getByText('查看详情'))
  await ask(view)
  await view.findByText(/"model": "claude-x"/)
  expect(sendAgentControl).toHaveBeenCalledWith('topic1', 's1', { subtype: 'initialize' })
})

it('reads a file by the path a person gives', async () => {
  vi.mocked(sendAgentControl).mockResolvedValue({
    request_id: 'r1',
    status: 'completed',
    result: { response: { subtype: 'success', response: { contents: '# 草稿' } } },
  })
  const view = mount()
  await view.findByText('会话已连接')
  await fireEvent.click(view.getByText('查看详情'))
  await fireEvent.mouseDown(view.getByRole('combobox'))
  await fireEvent.click(await view.findByRole('option', { name: '查看文件' }))
  await fireEvent.update(view.getByLabelText('文件路径'), 'docs/draft.md')
  await ask(view)
  await view.findByText('# 草稿')
  expect(sendAgentControl).toHaveBeenCalledWith('topic1', 's1', {
    subtype: 'read_file',
    path: 'docs/draft.md',
    encoding: 'utf8',
  })
})

it('shows why the session could not answer', async () => {
  vi.mocked(sendAgentControl).mockResolvedValue({
    request_id: 'r1',
    status: 'completed',
    result: { response: { subtype: 'error', error: 'No session is running in this room' } },
  })
  const view = mount()
  await view.findByText('会话已连接')
  await fireEvent.click(view.getByText('查看详情'))
  await ask(view)
  await view.findByText('No session is running in this room')
})

it('takes the room session state off the socket instead of asking again', async () => {
  // The room already holds a socket, so a task starting arrives as a frame.
  // What this pins is the half that saves the requests: having been given one,
  // the panel does not go back to asking every two seconds.
  vi.useFakeTimers({ shouldAdvanceTime: true })
  const view = render(Host, {
    props: { topicId: 'topic1', active: true, pushed: null },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
  await view.rerender({
    topicId: 'topic1',
    active: true,
    pushed: {
      id: 's2',
      connected: true,
      tasks: { t1: { task_id: 't1', description: '长命令', status: 'running', task_type: 'local_bash' } },
    },
  })
  await fireEvent.click(view.getByText('查看详情'))
  await view.findByText('长命令 · 运行中')
  await vi.advanceTimersByTimeAsync(6000)
  expect(getAgentControl).toHaveBeenCalledTimes(1)
})

it('names the teammates when several are working, and reads the one picked', async () => {
  const seats = [
    { agent: 'cheese-a', id: 'conv-a' },
    { agent: 'cheese-b', id: 'conv-b' },
  ]
  vi.mocked(getAgentControl).mockImplementation(async (_topic, agent) =>
    agent === 'cheese-b'
      ? { id: 'conv-b', connected: true, agent: 'cheese-b', seats }
      : { id: null, connected: false, agent: null, seats }
  )
  vi.mocked(sendAgentControl).mockResolvedValue({
    request_id: 'r1',
    status: 'completed',
    result: { response: { subtype: 'success', response: { model: 'claude-b' } } },
  })
  const view = mount()
  await view.findByText('频道里有 2 个会话在运行，选一位队友查看')
  expect(view.queryByText('没有在运行的会话')).toBeNull()
  // Details are folded, so the teammate picker is the only choice on screen.
  await fireEvent.mouseDown(view.getByRole('combobox'))
  await fireEvent.click(await view.findByRole('option', { name: 'cheese-b' }))
  await view.findByText('会话已连接')
  await fireEvent.click(view.getByText('查看详情'))
  await ask(view)
  await view.findByText(/"model": "claude-b"/)
  expect(sendAgentControl).toHaveBeenCalledWith('topic1', 'conv-b', { subtype: 'initialize' }, undefined, 'cheese-b')
})

it("says where each of the project's MCP servers comes from", async () => {
  const server = { host: 'mcp.example.test', auth: 'oauth', authorized_by: null, authorized_at: null } as const
  vi.mocked(getRoomMcpServers).mockResolvedValue({
    servers: [
      { ...server, name: 'tracker', status: 'ready', declared_by: null },
      {
        ...server,
        name: 'ticket',
        status: 'disconnected',
        declared_by: [
          { name: 'code-review', title: '代码评审' },
          { name: 'auditor', title: '审计' },
        ],
      },
    ],
  })
  const view = mount()
  await fireEvent.click(await view.findByText('查看详情'))
  const list = await view.findByTestId('room-mcp-servers')
  const rows = within(list).getAllByRole('listitem')
  expect(rows[0].textContent).toContain('来自项目的 .mcp.json')
  // Only the file name is code; the sentence around it is the UI's own font.
  expect(within(rows[0]).getByText('.mcp.json').tagName).toBe('CODE')
  expect(within(rows[1]).getByTestId('mcp-source').querySelector('code')).toBeNull()
  expect(rows[1].textContent).toContain('由 代码评审、审计 类型声明')
})
