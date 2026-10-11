// 「项目默认模型」那一块的数据与动作：读一次、存一次，结果就地回执（saving/saved/error）。
import type { ProjectDefaultModel } from '@/api'

import { defineComponent, h, nextTick, ref } from 'vue'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const getProjectDefaultModel = vi.fn()
const setProjectDefaultModel = vi.fn()

vi.mock('@/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api')>()
  return {
    ...actual,
    getProjectDefaultModel: (...a: unknown[]) => getProjectDefaultModel(...a),
    setProjectDefaultModel: (...a: unknown[]) => setProjectDefaultModel(...a),
  }
})

import { useProjectDefaultModelSettings } from './useProjectDefaultModelSettings'

function state(overrides: Partial<ProjectDefaultModel> = {}): ProjectDefaultModel {
  return {
    model: null,
    subagent_model: null,
    deployment_default: 'sonnet',
    can_manage: true,
    choices: [
      { id: 'sonnet', label: 'Claude Sonnet 5' },
      { id: 'deepseek-flash', label: 'DeepSeek V4.1 Flash' },
    ],
    ...overrides,
  }
}

const tick = () => new Promise<void>((r) => setTimeout(r, 0))

function host(projectId: () => string = () => 'p', options?: Parameters<typeof useProjectDefaultModelSettings>[1]) {
  let model!: ReturnType<typeof useProjectDefaultModelSettings>
  const view = render(
    defineComponent({
      setup() {
        model = useProjectDefaultModelSettings(projectId, options)
        return () => h('div')
      },
    })
  )
  return { view, model }
}

beforeEach(() => {
  getProjectDefaultModel.mockReset().mockResolvedValue(state())
  setProjectDefaultModel.mockReset().mockImplementation(async (_p: string, model: string | null) => state({ model }))
})
afterEach(cleanup)

describe('useProjectDefaultModelSettings', () => {
  it('reads once on mount', async () => {
    const { model } = host()
    expect(getProjectDefaultModel).toHaveBeenCalledWith('p')
    await tick()
    expect(model.state.value).toEqual(state())
  })

  it('saves both selections and reports the receipt', async () => {
    const { model } = host()
    await tick()
    await model.save('deepseek-flash', null)
    expect(setProjectDefaultModel).toHaveBeenCalledWith('p', 'deepseek-flash', null)
    expect(model.state.value?.model).toBe('deepseek-flash')
    expect(model.saved.value).toBe(true)
    expect(model.saveError.value).toBe('')
  })

  it('does not save for someone who cannot manage the project', async () => {
    getProjectDefaultModel.mockResolvedValue(state({ can_manage: false }))
    const { model } = host()
    await tick()
    await model.save('deepseek-flash', null)
    expect(setProjectDefaultModel).not.toHaveBeenCalled()
  })

  it('reports the reason when a save fails', async () => {
    setProjectDefaultModel.mockRejectedValue(new Error('save down'))
    const { model } = host()
    await tick()
    await model.save('deepseek-flash', null)
    expect(model.saveError.value).toBe('save down')
    expect(model.saved.value).toBe(false)
  })

  it('keeps the reason when a read fails', async () => {
    getProjectDefaultModel.mockRejectedValue(new Error('down'))
    const { model } = host()
    await tick()
    expect(model.loadError.value).toBe('down')
  })

  it('waits for enabled to turn on before reading', async () => {
    const on = ref(false)
    host(() => 'p', { enabled: () => on.value })
    await tick()
    expect(getProjectDefaultModel).not.toHaveBeenCalled()
    on.value = true
    await nextTick()
    await tick()
    expect(getProjectDefaultModel).toHaveBeenCalledTimes(1)
  })
})
