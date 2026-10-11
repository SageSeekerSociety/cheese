import { defineComponent, h, nextTick, ref } from 'vue'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { useProjectComputeSettings } from './useProjectComputeSettings'
import { useProjectTaskNamingSettings } from './useProjectTaskNamingSettings'

import * as api from '@/api'
import { computeState, taskNamingState } from '@/views/demo/catalogSettingsDataFixtures'

vi.mock('@/api')

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: Error) => void
  const promise = new Promise<T>((yes, no) => {
    resolve = yes
    reject = no
  })
  return { promise, resolve, reject }
}
async function flush() {
  await nextTick()
  await Promise.resolve()
  await nextTick()
}
function host<T>(create: (enabled: () => boolean) => T) {
  const active = ref(true)
  let state!: T
  render(
    defineComponent({
      setup() {
        state = create(() => active.value)
        return () => h('div')
      },
    })
  )
  return { state, active }
}

beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(api.getProjectComputeConfigs).mockResolvedValue(computeState())
  vi.mocked(api.getTaskNaming).mockResolvedValue(taskNamingState())
})
afterEach(cleanup)

it.each(['success', 'failure'])('isolates computer saves across projects after old %s', async (outcome) => {
  const project = ref('a')
  const old = deferred<Awaited<ReturnType<typeof api.saveProjectComputeConfigs>>>()
  const fresh = deferred<Awaited<ReturnType<typeof api.saveProjectComputeConfigs>>>()
  const nextProject = deferred<Awaited<ReturnType<typeof api.getProjectComputeConfigs>>>()
  vi.mocked(api.saveProjectComputeConfigs).mockReturnValueOnce(old.promise).mockReturnValueOnce(fresh.promise)
  const view = host((enabled) => useProjectComputeSettings(() => project.value, { enabled }))
  await flush()
  view.state.toggleEditing()
  const first = view.state.save({ kind: 'sandbox', spec: 'old' })
  vi.mocked(api.getProjectComputeConfigs).mockReturnValueOnce(nextProject.promise)
  project.value = 'b'
  await flush()
  expect(view.state.busy.value).toBe(false)
  expect(view.state.editing.value).toBe(false)
  expect(view.state.state.value).toBeNull()
  nextProject.resolve(computeState())
  await flush()
  view.state.toggleEditing()
  const second = view.state.save({ kind: 'sandbox', spec: 'vm' })
  expect(api.saveProjectComputeConfigs).toHaveBeenLastCalledWith('b', { default: { kind: 'sandbox', spec: 'vm' } })
  if (outcome === 'success') old.resolve({ default: { kind: 'sandbox', spec: 'old' } })
  else old.reject(new Error('A failed'))
  await first
  expect(view.state.state.value?.default).toEqual(computeState().default)
  expect(view.state.busy.value).toBe(true)
  expect(view.state.editing.value).toBe(true)
  expect(view.state.error.value).toBe('')
  fresh.resolve({ default: { kind: 'sandbox', spec: 'vm' } })
  await second
  expect(view.state.state.value?.default).toEqual({ kind: 'sandbox', spec: 'vm' })
  expect(view.state.busy.value).toBe(false)
  expect(view.state.editing.value).toBe(false)
})

it.each(['success', 'failure'])('isolates task naming saves across projects after old %s', async (outcome) => {
  const project = ref('a')
  const old = deferred<Awaited<ReturnType<typeof api.setTaskNaming>>>()
  const fresh = deferred<Awaited<ReturnType<typeof api.setTaskNaming>>>()
  const nextProject = deferred<Awaited<ReturnType<typeof api.getTaskNaming>>>()
  vi.mocked(api.setTaskNaming).mockReturnValueOnce(old.promise).mockReturnValueOnce(fresh.promise)
  const view = host((enabled) => useProjectTaskNamingSettings(() => project.value, { enabled }))
  await flush()
  const first = view.state.choose('manual')
  vi.mocked(api.getTaskNaming).mockReturnValueOnce(nextProject.promise)
  project.value = 'b'
  await flush()
  expect(view.state.busy.value).toBe(false)
  expect(view.state.state.value).toBeNull()
  nextProject.resolve(taskNamingState())
  await flush()
  const second = view.state.choose('manual')
  expect(api.setTaskNaming).toHaveBeenLastCalledWith('b', 'manual')
  if (outcome === 'success') old.resolve(taskNamingState({ mode: 'manual' }))
  else old.reject(new Error('A failed'))
  await first
  expect(view.state.state.value?.mode).toBe('auto')
  expect(view.state.busy.value).toBe(true)
  expect(view.state.error.value).toBe('')
  fresh.resolve(taskNamingState({ mode: 'manual' }))
  await second
  expect(view.state.state.value?.mode).toBe('manual')
  expect(view.state.busy.value).toBe(false)
})

it.each(['success', 'failure'])(
  'keeps a computer save busy through a distribution refresh until %s',
  async (outcome) => {
    const pending = deferred<Awaited<ReturnType<typeof api.saveProjectComputeConfigs>>>()
    vi.mocked(api.saveProjectComputeConfigs).mockReturnValueOnce(pending.promise)
    const view = host((enabled) => useProjectComputeSettings(() => 'p', { enabled }))
    await flush()
    const saving = view.state.save({ kind: 'sandbox', spec: 'vm' })
    const refreshed = computeState()
    refreshed.distribution.sandboxes[0].agents = 7
    vi.mocked(api.getProjectComputeConfigs).mockResolvedValueOnce(refreshed)
    await view.state.load()
    expect(view.state.busy.value).toBe(true)
    expect(view.state.state.value?.distribution.sandboxes[0].agents).toBe(7)
    if (outcome === 'success') pending.resolve({ default: { kind: 'sandbox', spec: 'vm' } })
    else pending.reject(new Error('save failed'))
    await saving
    expect(view.state.busy.value).toBe(false)
    expect(view.state.state.value?.default).toEqual(
      outcome === 'success' ? { kind: 'sandbox', spec: 'vm' } : refreshed.default
    )
    expect(view.state.error.value).toBe(outcome === 'success' ? '' : 'save failed')
    expect(view.state.state.value?.distribution.sandboxes[0].agents).toBe(7)
  }
)

it.each(['before', 'during'])('keeps a saved default when a refresh started %s saving returns late', async (when) => {
  const read = deferred<Awaited<ReturnType<typeof api.getProjectComputeConfigs>>>()
  const pending = deferred<Awaited<ReturnType<typeof api.saveProjectComputeConfigs>>>()
  vi.mocked(api.saveProjectComputeConfigs).mockReturnValueOnce(pending.promise)
  const view = host((enabled) => useProjectComputeSettings(() => 'p', { enabled }))
  await flush()
  vi.mocked(api.getProjectComputeConfigs).mockReturnValueOnce(read.promise)
  let loading: Promise<void>
  let saving: Promise<void>
  if (when === 'before') {
    loading = view.state.load()
    saving = view.state.save({ kind: 'sandbox', spec: 'vm' })
  } else {
    saving = view.state.save({ kind: 'sandbox', spec: 'vm' })
    loading = view.state.load()
  }
  pending.resolve({ default: { kind: 'sandbox', spec: 'vm' } })
  await saving
  expect(view.state.state.value?.default).toEqual({ kind: 'sandbox', spec: 'vm' })
  const refreshed = computeState()
  refreshed.distribution.sandboxes[0].agents = 7
  read.resolve(refreshed)
  await loading
  expect(view.state.state.value?.default).toEqual({ kind: 'sandbox', spec: 'vm' })
  expect(view.state.state.value?.distribution.sandboxes[0].agents).toBe(7)
  expect(view.state.busy.value).toBe(false)
})

it.each(['success', 'failure'])('keeps a task naming save busy through a refresh until %s', async (outcome) => {
  const pending = deferred<Awaited<ReturnType<typeof api.setTaskNaming>>>()
  vi.mocked(api.setTaskNaming).mockReturnValueOnce(pending.promise)
  const view = host((enabled) => useProjectTaskNamingSettings(() => 'p', { enabled }))
  await flush()
  const saving = view.state.choose('manual')
  await view.state.load()
  expect(view.state.busy.value).toBe(true)
  if (outcome === 'success') pending.resolve(taskNamingState({ mode: 'manual' }))
  else pending.reject(new Error('save failed'))
  await saving
  expect(view.state.busy.value).toBe(false)
  expect(view.state.state.value?.mode).toBe(outcome === 'success' ? 'manual' : 'auto')
  expect(view.state.error.value).toBe(outcome === 'success' ? '' : 'save failed')
})

it('keeps a saved task naming mode when an earlier refresh returns late', async () => {
  const read = deferred<Awaited<ReturnType<typeof api.getTaskNaming>>>()
  const pending = deferred<Awaited<ReturnType<typeof api.setTaskNaming>>>()
  vi.mocked(api.setTaskNaming).mockReturnValueOnce(pending.promise)
  const view = host((enabled) => useProjectTaskNamingSettings(() => 'p', { enabled }))
  await flush()
  vi.mocked(api.getTaskNaming).mockReturnValueOnce(read.promise)
  const saving = view.state.choose('manual')
  const loading = view.state.load()
  pending.resolve(taskNamingState({ mode: 'manual' }))
  await saving
  read.resolve(taskNamingState())
  await loading
  expect(view.state.state.value?.mode).toBe('manual')
  expect(view.state.busy.value).toBe(false)
})

it.each(['success', 'failure'])('keeps a new computer save busy after an old section save %s', async (outcome) => {
  const old = deferred<Awaited<ReturnType<typeof api.saveProjectComputeConfigs>>>()
  const fresh = deferred<Awaited<ReturnType<typeof api.saveProjectComputeConfigs>>>()
  vi.mocked(api.saveProjectComputeConfigs).mockReturnValueOnce(old.promise).mockReturnValueOnce(fresh.promise)
  const view = host((enabled) => useProjectComputeSettings(() => 'p', { enabled }))
  await flush()
  const first = view.state.save({ kind: 'sandbox', spec: 'old' })
  view.active.value = false
  await flush()
  view.active.value = true
  await flush()
  const second = view.state.save({ kind: 'sandbox', spec: 'vm' })
  if (outcome === 'success') old.resolve({ default: { kind: 'sandbox', spec: 'old' } })
  else old.reject(new Error('old failed'))
  await first
  expect(view.state.busy.value).toBe(true)
  expect(view.state.error.value).toBe('')
  expect(view.state.state.value?.default).toEqual(computeState().default)
  fresh.resolve({ default: { kind: 'sandbox', spec: 'vm' } })
  await second
  expect(view.state.busy.value).toBe(false)
  expect(view.state.state.value?.default).toEqual({ kind: 'sandbox', spec: 'vm' })
})
