import type { ComputeChoice, SessionWorkLease, TopicComputeProfile } from '../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  getSessionWorkLeases: vi.fn(),
  setSessionWorkChoice: vi.fn(),
  approveSessionWorkChoice: vi.fn(),
  getSessionDispatches: vi.fn(),
  confirmSessionDispatch: vi.fn(),
}))
vi.mock('../api', () => api)
vi.mock('../services/account', async () => ({ currentUserName: (await import('vue')).ref('manager') }))

import SessionWorkPicker from './SessionWorkPicker.vue'

const cloud: ComputeChoice = {
  name: '云端',
  profile: 'cloud',
  device_id: null,
  cores: null,
  memory_mb: null,
  disk_gb: null,
}
const device: ComputeChoice = { ...cloud, name: '测试工作站', profile: 'device', device_id: 'workstation' }
const profile: TopicComputeProfile = {
  choice: cloud,
  project_default: cloud,
  favorites: [device],
  current: 'cloud',
  device_id: null,
  devices: [{ device_id: 'workstation', name: '测试工作站', online: true }],
  locked: true,
  inherited: false,
  profiles: [],
  visibility: { options: [], effective: null, machine_access: false, notice: '' },
}
function session(overrides: Partial<SessionWorkLease> = {}): SessionWorkLease {
  return {
    id: 'session-a',
    agent_handle: 'analyst',
    harness: 'test-harness',
    choice: cloud,
    lease: { device_id: 'old-cloud', generation: 1, status: 'ready', online: true },
    pending: { id: 'proposal-a', choice: device, approver: 'manager', source_generation: 1 },
    ...overrides,
  }
}
async function open() {
  render(SessionWorkPicker, {
    props: { topicId: 'room-a', profile },
    global: { plugins: [createVuetify({ components, directives })] },
  })
  await fireEvent.click(screen.getByRole('button', { name: '会话执行机器' }))
  await screen.findByText('当前配置：云端')
}

beforeEach(() => {
  vi.resetAllMocks()
  vi.stubGlobal('devicePixelRatio', 1)
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    addEventListener() {},
    removeEventListener() {},
  })
  vi.stubGlobal('matchMedia', () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  }))
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
  api.getSessionWorkLeases.mockResolvedValue({ sessions: [session()] })
  api.getSessionDispatches.mockResolvedValue({ dispatches: [] })
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('session work machine approval', () => {
  it('requires an explicit acknowledgement for an unreachable old machine', async () => {
    api.getSessionWorkLeases.mockResolvedValue({
      sessions: [session({ lease: { ...session().lease!, online: false } })],
    })
    await open()
    const approve = screen.getByRole('button', { name: '批准变更' })
    expect(approve.hasAttribute('disabled')).toBe(true)
    await fireEvent.input(screen.getByRole('checkbox', { name: '我已核实旧机器上的未完成工作，确认换机' }), {
      target: { checked: true },
    })
    await waitFor(() =>
      expect(
        (screen.getByRole('checkbox', { name: '我已核实旧机器上的未完成工作，确认换机' }) as HTMLInputElement).checked
      ).toBe(true)
    )
    await waitFor(() => expect(approve.hasAttribute('disabled')).toBe(false))
    api.approveSessionWorkChoice.mockResolvedValue({ session: session({ pending: null }) })
    await fireEvent.click(approve)
    await waitFor(() =>
      expect(api.approveSessionWorkChoice).toHaveBeenCalledWith('room-a', 'session-a', 'proposal-a', true)
    )
  })
  it('shows an unknown operation without offering confirmation to a non-manager', async () => {
    await open()
    api.getSessionDispatches.mockResolvedValue({
      can_confirm: false,
      dispatches: [
        { id: 'dispatch-a', key: 'operation-a', tool: 'Bash', outcome: null, dispatched_at: null, confirmed_at: null },
      ],
    })
    await fireEvent.click(screen.getByRole('button', { name: '查看待核实操作' }))
    expect(await screen.findByText(/Bash · 发送时间未知/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: '已核实，保存记录' })).toBeNull()
  })
  it('targets the selected teammate session instead of the room default', async () => {
    api.getSessionWorkLeases.mockResolvedValue({
      sessions: [
        session(),
        session({ id: 'session-b', agent_handle: 'builder', pending: { ...session().pending!, id: 'proposal-b' } }),
      ],
    })
    await open()
    await fireEvent.mouseDown(screen.getByLabelText('队友会话'))
    await fireEvent.click(await screen.findByRole('option', { name: 'builder · session-' }))
    api.setSessionWorkChoice.mockResolvedValue({
      session: session({
        id: 'session-b',
        agent_handle: 'builder',
        pending: { ...session().pending!, id: 'proposal-b' },
      }),
    })
    await fireEvent.click(screen.getByRole('button', { name: '使用此配置' }))
    await waitFor(() => expect(api.setSessionWorkChoice).toHaveBeenCalledWith('room-a', 'session-b', device))
    api.approveSessionWorkChoice.mockResolvedValue({ session: session({ id: 'session-b', pending: null }) })
    await fireEvent.click(screen.getByRole('button', { name: '批准变更' }))
    await waitFor(() =>
      expect(api.approveSessionWorkChoice).toHaveBeenCalledWith('room-a', 'session-b', 'proposal-b', false)
    )
  })
  it('approves the displayed session and exact proposal, then reloads the authoritative choice', async () => {
    await open()
    api.approveSessionWorkChoice.mockResolvedValue({ session: session({ choice: device, pending: null, lease: null }) })
    api.getSessionWorkLeases.mockResolvedValue({ sessions: [session({ choice: device, pending: null, lease: null })] })
    await fireEvent.click(screen.getByRole('button', { name: '批准变更' }))
    await waitFor(() =>
      expect(api.approveSessionWorkChoice).toHaveBeenCalledWith('room-a', 'session-a', 'proposal-a', false)
    )
    expect(await screen.findByText('当前配置：测试工作站')).toBeTruthy()
    expect(screen.getByText('首次执行操作时准备机器')).toBeTruthy()
    expect(screen.queryByRole('button', { name: '批准变更' })).toBeNull()
  })
  it('keeps the current machine visible when a choice becomes a proposal', async () => {
    await open()
    api.setSessionWorkChoice.mockResolvedValue({ session: session() })
    await fireEvent.click(screen.getByRole('button', { name: '使用此配置' }))
    await waitFor(() => expect(api.setSessionWorkChoice).toHaveBeenCalledWith('room-a', 'session-a', device))
    expect(screen.getByText('当前配置：云端')).toBeTruthy()
    expect(screen.getByText('待批准：测试工作站')).toBeTruthy()
  })
  it('shows who must approve without offering another user an approval button', async () => {
    api.getSessionWorkLeases.mockResolvedValue({
      sessions: [session({ pending: { ...session().pending!, approver: 'owner' } })],
    })
    await open()
    expect(screen.getByText('批准人：owner')).toBeTruthy()
    expect(screen.queryByRole('button', { name: '批准变更' })).toBeNull()
  })
  it('preserves the pending proposal after a rejected approval and records a deliberate unknown-operation confirmation', async () => {
    await open()
    api.approveSessionWorkChoice.mockRejectedValue(new Error('仍有结果未知的操作'))
    await fireEvent.click(screen.getByRole('button', { name: '批准变更' }))
    expect((await screen.findByText('仍有结果未知的操作')).getAttribute('role')).toBe('alert')
    expect(screen.getByText('当前配置：云端')).toBeTruthy()
    api.getSessionDispatches.mockResolvedValue({
      can_confirm: true,
      dispatches: [
        {
          id: 'dispatch-a',
          key: 'operation-a',
          tool: 'Bash',
          outcome: null,
          dispatched_at: '2026-09-22T10:00:00Z',
          confirmed_at: null,
        },
      ],
    })
    await fireEvent.click(screen.getByRole('button', { name: '查看待核实操作' }))
    const confirmation = await screen.findByRole('button', { name: '已核实，保存记录' })
    expect(confirmation.hasAttribute('disabled')).toBe(true)
    await fireEvent.update(screen.getByLabelText('核实情况'), '已检查旧机器，命令已结束且输出已保存。')
    api.confirmSessionDispatch.mockResolvedValue({})
    api.getSessionDispatches.mockResolvedValue({ dispatches: [] })
    await fireEvent.click(confirmation)
    await waitFor(() =>
      expect(api.confirmSessionDispatch).toHaveBeenCalledWith(
        'room-a',
        'session-a',
        'dispatch-a',
        '已检查旧机器，命令已结束且输出已保存。'
      )
    )
    expect(api.approveSessionWorkChoice).toHaveBeenCalledTimes(1)
  })
})
