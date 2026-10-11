import { defineComponent, h, nextTick, ref } from 'vue'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { useProjectComputeSettings } from './useProjectComputeSettings'
import { useProjectDefaultModelSettings } from './useProjectDefaultModelSettings'
import { useProjectEnvironmentSettings } from './useProjectEnvironmentSettings'
import { useProjectMcpSettings } from './useProjectMcpSettings'
import { useProjectTaskNamingSettings } from './useProjectTaskNamingSettings'

import * as api from '@/api'
import {
  computeState,
  defaultModelState,
  envDiagnosis,
  envInfo,
  envStatus,
  mcpList,
  taskNamingState,
} from '@/views/demo/catalogSettingsDataFixtures'

vi.mock('@/api')
vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

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
  return {
    state,
    async leave() {
      active.value = false
      await flush()
    },
    async enter() {
      active.value = true
      await flush()
    },
  }
}

beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(api.getProjectComputeConfigs).mockResolvedValue(computeState())
  vi.mocked(api.getProjectDefaultModel).mockResolvedValue(defaultModelState())
  vi.mocked(api.getProjectEnvironment).mockResolvedValue(envInfo())
  vi.mocked(api.getRoomEnvironment).mockResolvedValue(envStatus())
  vi.mocked(api.getMcpServers).mockResolvedValue(mcpList())
  vi.mocked(api.getTaskNaming).mockResolvedValue(taskNamingState())
})
afterEach(cleanup)

it.each(['success', 'failure'])('ignores an old computer save %s after leave/reentry', async (outcome) => {
  const old = deferred<Awaited<ReturnType<typeof api.saveProjectComputeConfigs>>>()
  const fresh = deferred<Awaited<ReturnType<typeof api.saveProjectComputeConfigs>>>()
  vi.mocked(api.saveProjectComputeConfigs).mockReturnValueOnce(old.promise).mockReturnValueOnce(fresh.promise)
  const view = host((enabled) => useProjectComputeSettings(() => 'p', { enabled }))
  await flush()
  const first = view.state.save({ name: 'old', profile: 'cloud', device_id: null })
  await view.leave()
  // An old partial response must not resurrect state while the component is absent.
  if (outcome === 'success') old.resolve({ default: { name: 'old', profile: 'cloud', device_id: null } })
  else old.reject(new Error('old failure'))
  await first
  expect(view.state.state.value).toBeNull()
  expect(view.state.error.value).toBe('')
  await view.enter()
  expect(view.state.state.value?.distribution.cloud).toBe(3)
  const second = view.state.save({ name: 'new', profile: 'cloud', device_id: null })
  expect(view.state.busy.value).toBe(true)
  fresh.resolve({ default: { name: 'new', profile: 'cloud', device_id: null } })
  await second
  expect(view.state.state.value?.default.name).toBe('new')
})

it.each(['success', 'failure'])('keeps a new model save busy when the old save returns %s', async (outcome) => {
  const old = deferred<ReturnType<typeof defaultModelState>>()
  const fresh = deferred<ReturnType<typeof defaultModelState>>()
  vi.mocked(api.setProjectDefaultModel).mockReturnValueOnce(old.promise).mockReturnValueOnce(fresh.promise)
  const view = host((enabled) => useProjectDefaultModelSettings(() => 'p', { enabled }))
  await flush()
  const first = view.state.save('old', null)
  await view.leave()
  await view.enter()
  const second = view.state.save('new', null)
  if (outcome === 'success') old.resolve(defaultModelState({ model: 'old' }))
  else old.reject(new Error('old failure'))
  await first
  expect(view.state.state.value?.model).toBeNull()
  expect(view.state.saved.value).toBe(false)
  expect(view.state.saveError.value).toBe('')
  expect(view.state.saving.value).toBe(true)
  fresh.resolve(defaultModelState({ model: 'new' }))
  await second
  expect(view.state.state.value?.model).toBe('new')
  expect(view.state.saved.value).toBe(true)
})

it.each(['success', 'failure'])('keeps current environment config and receipt after old save %s', async (outcome) => {
  const old = deferred<ReturnType<typeof envInfo>['config']>()
  const fresh = deferred<ReturnType<typeof envInfo>['config']>()
  vi.mocked(api.saveProjectEnvironment).mockReturnValueOnce(old.promise).mockReturnValueOnce(fresh.promise)
  const view = host((enabled) => useProjectEnvironmentSettings(() => 'p', { enabled, diagnoseRoom: vi.fn() }))
  await flush()
  const first = view.state.save({ setup_script: 'OLD', startup_script: '', variables: [] })
  await view.leave()
  await view.enter()
  const second = view.state.save({ setup_script: 'NEW', startup_script: '', variables: [] })
  if (outcome === 'success') old.resolve({ ...envInfo().config, setup_script: 'OLD' })
  else old.reject(new Error('old failure'))
  await first
  expect(view.state.info.value?.config.setup_script).toBe(envInfo().config.setup_script)
  expect(view.state.saved.value).toBe(false)
  expect(view.state.saveError.value).toBe('')
  expect(view.state.saving.value).toBe(true)
  fresh.resolve({ ...envInfo().config, setup_script: 'NEW' })
  await second
  expect(view.state.info.value?.config.setup_script).toBe('NEW')
  expect(view.state.saved.value).toBe(true)
})

it.each(['success', 'failure'])('ignores an old diagnosis %s after returning to the same room', async (outcome) => {
  const old = deferred<ReturnType<typeof envDiagnosis>>()
  const fresh = deferred<ReturnType<typeof envDiagnosis>>()
  const diagnoseRoom = vi.fn().mockReturnValueOnce(old.promise).mockReturnValueOnce(fresh.promise)
  const view = host((enabled) => useProjectEnvironmentSettings(() => 'p', { enabled, diagnoseRoom }))
  await flush()
  const first = view.state.diagnose()
  expect(diagnoseRoom).toHaveBeenCalledWith('r-general')
  await view.leave()
  await view.enter()
  const second = view.state.diagnose()
  if (outcome === 'success') old.resolve(envDiagnosis({ reason: 'OLD ATTEMPT' }))
  else old.reject(new Error('old failure'))
  await first
  expect(view.state.diagnosis.value).toBeNull()
  expect(view.state.error.value).toBe('')
  expect(view.state.diagnosing.value).toBe(true)
  fresh.resolve(envDiagnosis({ reason: 'CURRENT ATTEMPT' }))
  await second
  expect(view.state.diagnosis.value?.reason).toBe('CURRENT ATTEMPT')
  expect(view.state.diagnosing.value).toBe(false)
  await view.leave()
  await view.enter()
  expect(view.state.diagnosis.value).toBeNull()
})

it('does not clear a new MCP secret or release its busy state when an old save returns', async () => {
  const old = deferred<void>()
  const fresh = deferred<void>()
  vi.mocked(api.setMcpSecret).mockReturnValueOnce(old.promise).mockReturnValueOnce(fresh.promise)
  const view = host((enabled) => useProjectMcpSettings(() => 'p', { enabled }))
  await flush()
  view.state.setValue('SEARCH_KEY', 'first')
  const first = view.state.save('SEARCH_KEY')
  await view.leave()
  await view.enter()
  view.state.setValue('SEARCH_KEY', 'new-unsaved')
  const second = view.state.save('SEARCH_KEY')
  old.resolve()
  await first
  expect(view.state.values.SEARCH_KEY).toBe('new-unsaved')
  expect(view.state.busy.value).toBe('SEARCH_KEY')
  expect(api.getMcpServers).toHaveBeenCalledTimes(2)
  fresh.resolve()
  await second
  expect(view.state.values.SEARCH_KEY).toBe('')
  expect(view.state.busy.value).toBe('')
})

it('does not accept an old task-naming save after returning', async () => {
  const old = deferred<ReturnType<typeof taskNamingState>>()
  vi.mocked(api.setTaskNaming).mockReturnValueOnce(old.promise)
  const view = host((enabled) => useProjectTaskNamingSettings(() => 'p', { enabled }))
  await flush()
  const first = view.state.choose('manual')
  await view.leave()
  await view.enter()
  old.resolve(taskNamingState({ mode: 'manual' }))
  await first
  expect(view.state.state.value?.mode).toBe('auto')
  expect(view.state.error.value).toBe('')
})
