import type { ComputeChoice, SessionWorkLease, TopicComputeProfile } from '../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  getSessionWorkLeases: vi.fn(),
  setSessionWorkChoice: vi.fn(),
}))
vi.mock('../api', () => api)
import { setLocale } from '../i18n'

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
    ...overrides,
  }
}
async function open(computeProfile = profile) {
  render(SessionWorkPicker, {
    props: { topicId: 'room-a', profile: computeProfile },
    global: { plugins: [createVuetify({ components, directives })] },
  })
  await fireEvent.click(screen.getByRole('button', { name: '更换工作电脑' }))
  await screen.findByText('当前电脑：云端')
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
  setLocale('zh-CN')
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

async function chooseMachine() {
  await fireEvent.mouseDown(screen.getByLabelText('工作电脑'))
  await fireEvent.click(await screen.findByRole('option', { name: '测试工作站' }))
}

describe('session work machine selection', () => {
  it('changes the selected teammate only after explicit confirmation', async () => {
    api.getSessionWorkLeases.mockResolvedValue({
      sessions: [session(), session({ id: 'session-b', agent_handle: 'builder' })],
    })
    await open()
    expect(screen.getByRole('button', { name: '确认更换' }).hasAttribute('disabled')).toBe(true)
    await fireEvent.mouseDown(screen.getByLabelText('队友'))
    await fireEvent.click(await screen.findByRole('option', { name: 'builder' }))
    await chooseMachine()
    expect(api.setSessionWorkChoice).not.toHaveBeenCalled()
    api.setSessionWorkChoice.mockResolvedValue({
      session: session({ id: 'session-b', agent_handle: 'builder', choice: device, lease: null }),
    })
    await fireEvent.click(screen.getByRole('button', { name: '确认更换' }))
    await waitFor(() => expect(api.setSessionWorkChoice).toHaveBeenCalledWith('room-a', 'session-b', device))
    expect(await screen.findByText('当前电脑：测试工作站')).toBeTruthy()
    expect(screen.getByRole('status').textContent).toContain('已更换')
    expect(api.setSessionWorkChoice).toHaveBeenCalledTimes(1)
  })

  it('does not offer extra approval or investigation steps when the old machine is offline', async () => {
    api.getSessionWorkLeases.mockResolvedValue({
      sessions: [session({ lease: { ...session().lease!, online: false } })],
    })
    await open()
    expect(screen.queryByLabelText('队友')).toBeNull()
    expect(screen.queryByRole('checkbox')).toBeNull()
    expect(screen.queryByText(/批准|待核实/)).toBeNull()
    await chooseMachine()
    api.setSessionWorkChoice.mockResolvedValue({ session: session({ choice: device, lease: null }) })
    await fireEvent.click(screen.getByRole('button', { name: '确认更换' }))
    await waitFor(() => expect(api.setSessionWorkChoice).toHaveBeenCalledTimes(1))
  })

  it('keeps the current machine and allows retry when changing fails', async () => {
    await open()
    await chooseMachine()
    api.setSessionWorkChoice.mockRejectedValue(new Error('设备已离线'))
    await fireEvent.click(screen.getByRole('button', { name: '确认更换' }))
    expect((await screen.findByText('设备已离线')).getAttribute('role')).toBe('alert')
    expect(screen.getByText('当前电脑：云端')).toBeTruthy()
    expect(screen.getByRole('button', { name: '确认更换' }).hasAttribute('disabled')).toBe(false)
  })

  it('clears an unconfirmed selection when changing teammates', async () => {
    api.getSessionWorkLeases.mockResolvedValue({
      sessions: [session(), session({ id: 'session-b', agent_handle: 'builder' })],
    })
    await open()
    await chooseMachine()
    await fireEvent.mouseDown(screen.getByLabelText('队友'))
    await fireEvent.click(await screen.findByRole('option', { name: 'builder' }))
    expect(screen.getByRole('button', { name: '确认更换' }).hasAttribute('disabled')).toBe(true)
    expect(api.setSessionWorkChoice).not.toHaveBeenCalled()
  })

  it('prevents duplicate changes while the request is in flight', async () => {
    await open()
    await chooseMachine()
    let finish!: (value: { session: SessionWorkLease }) => void
    api.setSessionWorkChoice.mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve
        })
    )
    await fireEvent.click(screen.getByRole('button', { name: '确认更换' }))
    expect(screen.getByRole('button', { name: '确认更换' }).hasAttribute('disabled')).toBe(true)
    expect(screen.getByLabelText('工作电脑').hasAttribute('disabled')).toBe(true)
    finish({ session: session({ choice: device, lease: null }) })
    expect(await screen.findByText('当前电脑：测试工作站')).toBeTruthy()
  })
})

describe('existing machine choices', () => {
  it('distinguishes same-name sessions by number without exposing runtime names', async () => {
    api.getSessionWorkLeases.mockResolvedValue({
      sessions: [session({ harness: 'test-runtime-a' }), session({ id: 'session-b', harness: 'test-runtime-b' })],
    })
    await open()
    await fireEvent.mouseDown(screen.getByLabelText('队友'))
    expect(await screen.findByRole('option', { name: 'analyst（会话 1）' })).toBeTruthy()
    expect(screen.queryByText(/test-runtime/)).toBeNull()
    await fireEvent.click(await screen.findByRole('option', { name: 'analyst（会话 2）' }))
    await chooseMachine()
    api.setSessionWorkChoice.mockResolvedValue({
      session: session({ id: 'session-b', harness: 'test-runtime-b', choice: device, lease: null }),
    })
    await fireEvent.click(screen.getByRole('button', { name: '确认更换' }))
    await waitFor(() => expect(api.setSessionWorkChoice).toHaveBeenCalledWith('room-a', 'session-b', device))
  })

  it('preserves the project default that selects a team device automatically', async () => {
    const automatic = { ...device, name: '自动选择团队设备', device_id: null }
    await open({ ...profile, project_default: automatic })
    await fireEvent.mouseDown(screen.getByLabelText('工作电脑'))
    await fireEvent.click(await screen.findByRole('option', { name: automatic.name }))
    expect(api.setSessionWorkChoice).not.toHaveBeenCalled()
    api.setSessionWorkChoice.mockResolvedValue({ session: session({ choice: automatic, lease: null }) })
    await fireEvent.click(screen.getByRole('button', { name: '确认更换' }))
    await waitFor(() => expect(api.setSessionWorkChoice).toHaveBeenCalledWith('room-a', 'session-a', automatic))
  })

  it('keeps a current custom choice even when it is not in the project presets', async () => {
    const custom = { ...device, name: '专用工作站', device_id: 'custom-machine' }
    api.getSessionWorkLeases.mockResolvedValue({ sessions: [session({ choice: custom })] })
    render(SessionWorkPicker, {
      props: { topicId: 'room-a', profile },
      global: { plugins: [createVuetify({ components, directives })] },
    })
    await fireEvent.click(screen.getByRole('button', { name: '更换工作电脑' }))
    expect(await screen.findByText('当前电脑：专用工作站')).toBeTruthy()
    expect(screen.getByRole('button', { name: '确认更换' }).hasAttribute('disabled')).toBe(true)
    await fireEvent.mouseDown(screen.getByLabelText('工作电脑'))
    expect(await screen.findByRole('option', { name: '专用工作站 · 不可用' })).toBeTruthy()
    expect(api.setSessionWorkChoice).not.toHaveBeenCalled()
  })
})
