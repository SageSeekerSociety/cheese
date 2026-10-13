// 「运行环境」那一块的数据与动作：读一次、每 5 秒轮询现状、保存（查重）、应用、诊断、
// 采用、换频道。读口是 `enabled`：离开这一栏就停表。
import type { EnvironmentStatus, ProjectEnvironmentInfo } from '@/cx_types'
import type { EnvironmentDiagnosis } from '@/types/environment'

import { defineComponent, h, nextTick, ref } from 'vue'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const getProjectEnvironment = vi.fn()
const getRoomEnvironment = vi.fn()
const saveProjectEnvironment = vi.fn()
const applyRoomEnvironment = vi.fn()

vi.mock('@/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api')>()
  return {
    ...actual,
    getProjectEnvironment: (...a: unknown[]) => getProjectEnvironment(...a),
    getRoomEnvironment: (...a: unknown[]) => getRoomEnvironment(...a),
    saveProjectEnvironment: (...a: unknown[]) => saveProjectEnvironment(...a),
    applyRoomEnvironment: (...a: unknown[]) => applyRoomEnvironment(...a),
  }
})

import { useProjectEnvironmentSettings } from './useProjectEnvironmentSettings'

import { setLocale } from '@/i18n'

const config = {
  setup_script: 'echo setup',
  startup_script: 'echo startup',
  variables: { CUSTOM: 'literal' },
  revision: 'rev-1',
}

function info(overrides: Partial<ProjectEnvironmentInfo> = {}): ProjectEnvironmentInfo {
  return {
    config: { ...config },
    can_edit: true,
    rooms: [
      { id: 'r-ok', title: 'Room', revision: 'rev-1' },
      {
        id: 'r-fail',
        title: 'Broken',
        revision: 'rev-1',
        failure: { attempt: 'a', at: '', stage: 'startup', exit_code: 1, log: '', waiting: 0 },
      },
    ],
    ...overrides,
  }
}

const status: EnvironmentStatus = { state: 'pending', pinned_revision: 'rev-1' }

const diagnosis: EnvironmentDiagnosis = {
  reason: 'dep missing',
  change: 'install',
  setup_script: null,
  startup_script: 'echo new',
  sure: true,
  ran: { setup_script: 'echo setup', startup_script: 'echo startup' },
}

const flush = () => vi.advanceTimersByTimeAsync(0)

function host(
  projectId: () => string,
  options: {
    enabled?: () => boolean
    room?: () => string | undefined
    diagnoseRoom?: (roomId: string) => Promise<EnvironmentDiagnosis>
  } = {}
) {
  let state!: ReturnType<typeof useProjectEnvironmentSettings>
  const view = render(
    defineComponent({
      setup() {
        state = useProjectEnvironmentSettings(projectId, {
          diagnoseRoom: options.diagnoseRoom ?? vi.fn(),
          enabled: options.enabled,
          room: options.room,
        })
        return () => h('div')
      },
    })
  )
  return { view, state }
}

beforeEach(() => {
  setLocale('zh-CN')
  vi.useFakeTimers()
  getProjectEnvironment.mockReset().mockResolvedValue(info())
  getRoomEnvironment.mockReset().mockResolvedValue(status)
  saveProjectEnvironment.mockReset().mockResolvedValue({ ...config, revision: 'rev-2' })
  applyRoomEnvironment.mockReset().mockResolvedValue({ state: 'pending' })
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('useProjectEnvironmentSettings', () => {
  it('reads once and starts on the failing room', async () => {
    const { state } = host(() => 'p')
    expect(getProjectEnvironment).toHaveBeenCalledWith('p')
    await flush()
    expect(state.info.value).not.toBeNull()
    expect(state.selectedRoom.value).toBe('r-fail')
  })

  it('starts on the room it was pointed at', async () => {
    const { state } = host(() => 'p', { room: () => 'r-ok' })
    await flush()
    expect(state.selectedRoom.value).toBe('r-ok')
  })

  it('polls the room status every 5 seconds', async () => {
    host(() => 'p')
    await flush()
    expect(getRoomEnvironment).toHaveBeenCalledWith('p', 'r-fail')
    const after = getRoomEnvironment.mock.calls.length
    await vi.advanceTimersByTimeAsync(5000)
    expect(getRoomEnvironment.mock.calls.length).toBe(after + 1)
    await vi.advanceTimersByTimeAsync(5000)
    expect(getRoomEnvironment.mock.calls.length).toBe(after + 2)
  })

  it('saves the scripts and collapses the variable rows into one map', async () => {
    const { state } = host(() => 'p')
    await flush()
    await state.save({
      setup_script: 'a',
      startup_script: 'b',
      variables: [
        { key: 'X', value: '1' },
        { key: 'Y', value: '2' },
      ],
    })
    expect(saveProjectEnvironment).toHaveBeenCalledWith('p', {
      setup_script: 'a',
      startup_script: 'b',
      variables: { X: '1', Y: '2' },
    })
    expect(state.saved.value).toBe(true)
  })

  it('refuses a blank or repeated variable name before sending', async () => {
    const { state } = host(() => 'p')
    await flush()
    await state.save({
      setup_script: 'a',
      startup_script: 'b',
      variables: [
        { key: 'X', value: '1' },
        { key: 'X', value: '2' },
      ],
    })
    expect(saveProjectEnvironment).not.toHaveBeenCalled()
    expect(state.saveError.value).toContain('重复')

    await state.save({ setup_script: 'a', startup_script: 'b', variables: [{ key: '', value: '1' }] })
    expect(saveProjectEnvironment).not.toHaveBeenCalled()
  })

  it('applies a revision to the selected room and re-reads its status', async () => {
    const { state } = host(() => 'p')
    await flush()
    await state.apply(true)
    expect(applyRoomEnvironment).toHaveBeenCalledWith('p', 'r-fail', true)
    expect(getRoomEnvironment).toHaveBeenLastCalledWith('p', 'r-fail')
  })

  it('asks the diagnosis port and shows the answer', async () => {
    const diagnoseRoom = vi.fn().mockResolvedValue(diagnosis)
    const { state } = host(() => 'p', { diagnoseRoom })
    await flush()
    await state.diagnose()
    expect(diagnoseRoom).toHaveBeenCalledWith('r-fail')
    expect(state.diagnosis.value).toEqual(diagnosis)
    expect(state.diagnosing.value).toBe(false)
  })

  it('adopting saves the scripts, clears the diagnosis, then retries', async () => {
    const { state } = host(() => 'p')
    await flush()
    await state.adopt({ setup_script: 'a', startup_script: 'echo new', variables: [{ key: 'X', value: '1' }] })
    expect(saveProjectEnvironment).toHaveBeenCalledWith('p', {
      setup_script: 'a',
      startup_script: 'echo new',
      variables: { X: '1' },
    })
    expect(state.diagnosis.value).toBeNull()
    expect(applyRoomEnvironment).toHaveBeenCalledWith('p', 'r-fail', true)
  })

  it('changing the room clears the old status and diagnosis and asks again', async () => {
    const { state } = host(() => 'p')
    await flush()
    state.diagnosis.value = diagnosis
    state.selectRoom('r-ok')
    await nextTick()
    expect(state.diagnosis.value).toBeNull()
    expect(getRoomEnvironment).toHaveBeenLastCalledWith('p', 'r-ok')
  })

  it('does not read or poll until enabled, and stops when it turns off', async () => {
    const on = ref(false)
    host(() => 'p', { enabled: () => on.value })
    await flush()
    expect(getProjectEnvironment).not.toHaveBeenCalled()

    on.value = true
    await nextTick()
    await flush()
    expect(getProjectEnvironment).toHaveBeenCalledTimes(1)

    const after = getRoomEnvironment.mock.calls.length
    on.value = false
    await nextTick()
    await vi.advanceTimersByTimeAsync(15000)
    expect(getRoomEnvironment.mock.calls.length).toBe(after)
  })

  it('keeps the reason when the status read fails', async () => {
    getRoomEnvironment.mockRejectedValue(new Error('status down'))
    const { state } = host(() => 'p')
    await flush()
    await flush()
    expect(state.error.value).toBe('status down')
  })

  it('re-reads when the project changes', async () => {
    const id = ref('p')
    host(() => id.value)
    await flush()
    id.value = 'q'
    await nextTick()
    await flush()
    expect(getProjectEnvironment).toHaveBeenLastCalledWith('q')
  })
})
