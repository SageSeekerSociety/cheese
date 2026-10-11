// 「工作电脑」那一块的数据与动作：读一次、存一次（存成后广播、把表单收起来）。
import type { Environment, ProjectComputeConfigs } from '@/types/compute'

import { defineComponent, h, nextTick, ref } from 'vue'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const getProjectComputeConfigs = vi.fn()
const saveProjectComputeConfigs = vi.fn()

vi.mock('@/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api')>()
  return {
    ...actual,
    getProjectComputeConfigs: (...a: unknown[]) => getProjectComputeConfigs(...a),
    saveProjectComputeConfigs: (...a: unknown[]) => saveProjectComputeConfigs(...a),
  }
})

import { useProjectComputeSettings } from './useProjectComputeSettings'

const cloud: Environment = { kind: 'sandbox', spec: 'standard' }
const lab: Environment = { kind: 'self_hosted', device_id: 'lab' }

function configs(overrides: Partial<ProjectComputeConfigs> = {}): ProjectComputeConfigs {
  return {
    default: cloud,
    can_manage: true,
    devices: [{ device_id: 'lab', name: '实验室工作站', online: true, owned: true }],
    specs: [{ spec: 'standard', label: '标准' }],
    distribution: { sandboxes: [], devices: [] },
    ...overrides,
  }
}

const tick = () => new Promise<void>((r) => setTimeout(r, 0))

function host(projectId: () => string = () => 'p', options?: Parameters<typeof useProjectComputeSettings>[1]) {
  let state!: ReturnType<typeof useProjectComputeSettings>
  const view = render(
    defineComponent({
      setup() {
        state = useProjectComputeSettings(projectId, options)
        return () => h('div')
      },
    })
  )
  return { view, state }
}

beforeEach(() => {
  getProjectComputeConfigs.mockReset().mockResolvedValue(configs())
  saveProjectComputeConfigs.mockReset().mockResolvedValue({ default: lab })
})
afterEach(cleanup)

describe('useProjectComputeSettings', () => {
  it('reads once on mount', async () => {
    const { state } = host()
    expect(getProjectComputeConfigs).toHaveBeenCalledWith('p')
    await tick()
    expect(state.state.value).toEqual(configs())
  })

  it('saves a new default, merges the answer and broadcasts', async () => {
    const updated = vi.fn()
    window.addEventListener('project-compute-updated', updated)
    const { state } = host()
    await tick()
    state.toggleEditing()
    expect(state.editing.value).toBe(true)
    await state.save(lab)

    expect(saveProjectComputeConfigs).toHaveBeenCalledWith('p', { default: lab })
    expect(saveProjectComputeConfigs.mock.calls[0][1]).not.toHaveProperty('favorites')
    expect(state.state.value?.default).toEqual(lab)
    expect(state.editing.value).toBe(false)
    expect(updated).toHaveBeenCalledTimes(1)
    window.removeEventListener('project-compute-updated', updated)
  })

  it('does not save for someone who cannot manage the project', async () => {
    getProjectComputeConfigs.mockResolvedValue(configs({ can_manage: false }))
    const { state } = host()
    await tick()
    await state.save(lab)
    expect(saveProjectComputeConfigs).not.toHaveBeenCalled()
  })

  it('keeps the form open and reports the reason when a save fails', async () => {
    saveProjectComputeConfigs.mockRejectedValue(new Error('save down'))
    const { state } = host()
    await tick()
    state.toggleEditing()
    await state.save(lab)
    expect(state.error.value).toBe('save down')
    expect(state.editing.value).toBe(true)
  })

  it('toggles the form open and shut', async () => {
    const { state } = host()
    state.toggleEditing()
    state.toggleEditing()
    expect(state.editing.value).toBe(false)
  })

  it('keeps the reason when a read fails', async () => {
    getProjectComputeConfigs.mockRejectedValue(new Error('down'))
    const { state } = host()
    await tick()
    expect(state.error.value).toBe('down')
  })

  it('waits for enabled to turn on before reading', async () => {
    const on = ref(false)
    host(() => 'p', { enabled: () => on.value })
    await tick()
    expect(getProjectComputeConfigs).not.toHaveBeenCalled()
    on.value = true
    await nextTick()
    await tick()
    expect(getProjectComputeConfigs).toHaveBeenCalledTimes(1)
  })
})
