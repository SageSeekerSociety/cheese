import { defineComponent, h, nextTick, ref } from 'vue'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useProjectComputeSettings } from './useProjectComputeSettings'
import { useProjectDefaultModelSettings } from './useProjectDefaultModelSettings'
import { useProjectEnvironmentSettings } from './useProjectEnvironmentSettings'
import { useProjectMcpSettings } from './useProjectMcpSettings'
import { useProjectTaskNamingSettings } from './useProjectTaskNamingSettings'
import { provideRevealGate } from './useRevealGate'

import * as api from '@/api'
import {
  computeState,
  defaultModelState,
  envInfo,
  mcpList,
  taskNamingState,
} from '@/views/demo/catalogSettingsDataFixtures'

vi.mock('@/api')
vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: Error) => void
  const promise = new Promise<T>((yes, no) => {
    resolve = yes
    reject = no
  })
  return { promise, resolve, reject }
}

const flush = async () => {
  await Promise.resolve()
  await nextTick()
  await Promise.resolve()
  await nextTick()
}
const cases = [
  { name: 'task naming', read: api.getTaskNaming, data: taskNamingState(), create: useProjectTaskNamingSettings },
  { name: 'compute', read: api.getProjectComputeConfigs, data: computeState(), create: useProjectComputeSettings },
  {
    name: 'default model',
    read: api.getProjectDefaultModel,
    data: defaultModelState(),
    create: useProjectDefaultModelSettings,
  },
  { name: 'MCP', read: api.getMcpServers, data: mcpList(), create: useProjectMcpSettings },
  {
    name: 'environment',
    read: api.getProjectEnvironment,
    data: envInfo(),
    create: (id: () => string, options: Parameters<typeof useProjectEnvironmentSettings>[1]) =>
      useProjectEnvironmentSettings(id, { ...options, diagnoseRoom: vi.fn() }),
  },
]

beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(api.getTaskNaming).mockResolvedValue(taskNamingState())
  vi.mocked(api.getProjectComputeConfigs).mockResolvedValue(computeState())
  vi.mocked(api.getProjectDefaultModel).mockResolvedValue(defaultModelState())
  vi.mocked(api.getMcpServers).mockResolvedValue(mcpList())
  vi.mocked(api.getProjectEnvironment).mockResolvedValue(envInfo({ rooms: [] }))
})
afterEach(cleanup)

describe.each(cases)('$name section lifecycle', ({ read, data, create }) => {
  it.each(['success', 'failure'])('waits for a newly opened section and releases after %s', async (outcome) => {
    const active = ref(false)
    const pending = deferred<typeof data>()
    vi.mocked(read).mockReturnValue(pending.promise as never)
    let gate!: ReturnType<typeof provideRevealGate>
    const view = render(
      defineComponent({
        setup() {
          gate = provideRevealGate()
          create(() => 'p', { enabled: () => active.value, revealGate: gate, diagnoseRoom: vi.fn() })
          return () => h('div', gate.revealed.value ? 'ready' : 'waiting')
        },
      })
    )
    expect(read).not.toHaveBeenCalled()
    expect(view.getByText('ready')).toBeTruthy()
    active.value = true
    await flush()
    expect(read).toHaveBeenCalledTimes(1)
    expect(view.getByText('waiting')).toBeTruthy()
    if (outcome === 'success') pending.resolve(data)
    else pending.reject(new Error('offline'))
    await flush()
    expect(view.getByText('ready')).toBeTruthy()
  })

  it('releases when leaving, then waits for the new read rather than the old response', async () => {
    const active = ref(true)
    const old = deferred<typeof data>()
    const fresh = deferred<typeof data>()
    vi.mocked(read)
      .mockReturnValueOnce(old.promise as never)
      .mockReturnValueOnce(fresh.promise as never)
    const view = render(
      defineComponent({
        setup() {
          const gate = provideRevealGate()
          create(() => 'p', { enabled: () => active.value, revealGate: gate, diagnoseRoom: vi.fn() })
          return () => h('div', gate.revealed.value ? 'ready' : 'waiting')
        },
      })
    )
    await flush()
    expect(view.getByText('waiting')).toBeTruthy()
    active.value = false
    await flush()
    expect(view.getByText('ready')).toBeTruthy()
    active.value = true
    await flush()
    old.resolve(data)
    await flush()
    expect(view.getByText('waiting')).toBeTruthy()
    fresh.resolve(data)
    await flush()
    expect(view.getByText('ready')).toBeTruthy()
  })
})

it('discards unsaved MCP secrets on leaving and does not submit them after returning', async () => {
  const active = ref(true)
  let state!: ReturnType<typeof useProjectMcpSettings>
  render(
    defineComponent({
      setup() {
        state = useProjectMcpSettings(() => 'p', { enabled: () => active.value })
        return () => h('div')
      },
    })
  )
  await flush()
  state.setValue('SEARCH_KEY', 'unsaved-secret')
  active.value = false
  await flush()
  expect(state.values.SEARCH_KEY).toBeUndefined()
  active.value = true
  await flush()
  await state.save('SEARCH_KEY')
  expect(api.setMcpSecret).not.toHaveBeenCalled()
})

it('collapses the computer choice form when leaving its section', async () => {
  const active = ref(true)
  let state!: ReturnType<typeof useProjectComputeSettings>
  render(
    defineComponent({
      setup() {
        state = useProjectComputeSettings(() => 'p', { enabled: () => active.value })
        return () => h('div')
      },
    })
  )
  await flush()
  state.toggleEditing()
  expect(state.editing.value).toBe(true)
  active.value = false
  await flush()
  active.value = true
  await flush()
  expect(state.editing.value).toBe(false)
})

it('clears a default-model save failure on leaving its section', async () => {
  const active = ref(true)
  let state!: ReturnType<typeof useProjectDefaultModelSettings>
  vi.mocked(api.setProjectDefaultModel).mockRejectedValue(new Error('save failed'))
  render(
    defineComponent({
      setup() {
        state = useProjectDefaultModelSettings(() => 'p', { enabled: () => active.value })
        return () => h('div')
      },
    })
  )
  await flush()
  await state.save(null, null)
  expect(state.saveError.value).toBe('save failed')
  active.value = false
  await flush()
  active.value = true
  await flush()
  expect(state.saveError.value).toBe('')
  expect(state.saved.value).toBe(false)
})

it('clears an environment save failure and diagnosis when leaving', async () => {
  const active = ref(true)
  let state!: ReturnType<typeof useProjectEnvironmentSettings>
  render(
    defineComponent({
      setup() {
        state = useProjectEnvironmentSettings(() => 'p', { enabled: () => active.value, diagnoseRoom: vi.fn() })
        return () => h('div')
      },
    })
  )
  await flush()
  await state.save({ setup_script: '', startup_script: '', variables: [{ key: '', value: '' }] })
  expect(state.saveError.value).not.toBe('')
  active.value = false
  await flush()
  active.value = true
  await flush()
  expect(state.saveError.value).toBe('')
  expect(state.diagnosis.value).toBeNull()
})
