// 给名册上的一个 AI 队友换工作电脑：换谁由那一行说了，这里只问换到哪一台。
import type { ComputeChoice, SessionWorkLease, TopicComputeProfile } from '../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
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
  sessions: [],
  profiles: [],
  visibility: { options: [], effective: null, machine_access: false, notice: '' },
}
function session(overrides: Partial<SessionWorkLease> = {}): SessionWorkLease & { choice: ComputeChoice } {
  return {
    id: 'session-a',
    agent_handle: 'analyst',
    harness: 'test-harness',
    choice: cloud,
    lease: { device_id: 'old-cloud', generation: 1, status: 'ready', online: true },
    ...overrides,
  } as SessionWorkLease & { choice: ComputeChoice }
}
async function open(computeProfile = profile, current = session()) {
  const view = render(SessionWorkPicker, {
    props: { topicId: 'room-a', profile: computeProfile, session: current, name: '分析员' },
    global: { plugins: [createVuetify({ components, directives })] },
  })
  await fireEvent.click(screen.getByRole('button', { name: '更换' }))
  await screen.findByText(`现在用：${current.choice.name}`)
  return view
}
function dialog() {
  return within(screen.getByRole('dialog'))
}
function confirmButton() {
  return dialog().getByRole('button', { name: '更换' })
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
  setLocale('zh-CN')
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

async function chooseMachine() {
  await fireEvent.mouseDown(screen.getByLabelText('换到'))
  await fireEvent.click(await screen.findByRole('option', { name: '测试工作站' }))
}

describe("changing one teammate's work computer", () => {
  it('names the teammate and changes only after explicit confirmation', async () => {
    const { emitted } = await open()
    expect(screen.getByText('给 分析员 换一台工作电脑')).toBeTruthy()
    expect(confirmButton().hasAttribute('disabled')).toBe(true)
    await chooseMachine()
    expect(screen.getByRole('note').textContent).toBe(
      '更换电脑可能中断正在运行的任务，并丢失未保存的工作。后续操作将使用新电脑。'
    )
    expect(api.setSessionWorkChoice).not.toHaveBeenCalled()
    api.setSessionWorkChoice.mockResolvedValue({ session: session({ choice: device, lease: null }) })
    await fireEvent.click(confirmButton())
    await waitFor(() => expect(api.setSessionWorkChoice).toHaveBeenCalledWith('room-a', 'session-a', device))
    expect(await screen.findByText('现在用：测试工作站')).toBeTruthy()
    expect(screen.getByRole('status').textContent).toContain('已更换')
    expect(api.setSessionWorkChoice).toHaveBeenCalledTimes(1)
    expect(emitted().changed).toHaveLength(1)
  })

  it('does not offer extra approval or investigation steps when the old machine is offline', async () => {
    await open(profile, session({ lease: { ...session().lease!, online: false } }))
    expect(screen.queryByRole('checkbox')).toBeNull()
    expect(screen.queryByText(/批准|待核实/)).toBeNull()
    await chooseMachine()
    api.setSessionWorkChoice.mockResolvedValue({ session: session({ choice: device, lease: null }) })
    await fireEvent.click(confirmButton())
    await waitFor(() => expect(api.setSessionWorkChoice).toHaveBeenCalledTimes(1))
  })

  it('keeps the current machine and allows retry when changing fails', async () => {
    await open()
    await chooseMachine()
    api.setSessionWorkChoice.mockRejectedValue(new Error('设备已离线'))
    await fireEvent.click(confirmButton())
    expect((await screen.findByText('设备已离线')).getAttribute('role')).toBe('alert')
    expect(screen.getByText('现在用：云端')).toBeTruthy()
    expect(confirmButton().hasAttribute('disabled')).toBe(false)
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
    await fireEvent.click(confirmButton())
    expect(confirmButton().hasAttribute('disabled')).toBe(true)
    expect(screen.getByLabelText('换到').hasAttribute('disabled')).toBe(true)
    finish({ session: session({ choice: device, lease: null }) })
    expect(await screen.findByText('现在用：测试工作站')).toBeTruthy()
  })
})

describe('existing machine choices', () => {
  it('preserves the project default that selects a team device automatically', async () => {
    const automatic = { ...device, name: '自动选择团队设备', device_id: null }
    await open({ ...profile, project_default: automatic })
    await fireEvent.mouseDown(screen.getByLabelText('换到'))
    await fireEvent.click(await screen.findByRole('option', { name: automatic.name }))
    expect(api.setSessionWorkChoice).not.toHaveBeenCalled()
    api.setSessionWorkChoice.mockResolvedValue({ session: session({ choice: automatic, lease: null }) })
    await fireEvent.click(confirmButton())
    await waitFor(() => expect(api.setSessionWorkChoice).toHaveBeenCalledWith('room-a', 'session-a', automatic))
  })

  it('keeps a current custom choice even when it is not in the project presets', async () => {
    const custom = { ...device, name: '专用工作站', device_id: 'custom-machine' }
    await open(profile, session({ choice: custom }))
    expect(confirmButton().hasAttribute('disabled')).toBe(true)
    await fireEvent.mouseDown(screen.getByLabelText('换到'))
    expect(await screen.findByRole('option', { name: '专用工作站 · 不可用' })).toBeTruthy()
    expect(api.setSessionWorkChoice).not.toHaveBeenCalled()
  })
})
