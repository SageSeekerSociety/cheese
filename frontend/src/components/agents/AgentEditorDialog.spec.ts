// Teammate validation and optional project-scoped model overrides.
import type { AgentType, ProjectAgent } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const createProjectAgent = vi.fn()
const updateProjectAgent = vi.fn()
const updateAgentType = vi.fn()
const createAgentType = vi.fn()
const setProjectDefaultAgent = vi.fn()
const getProjectDefaultModel = vi.fn()

const CHOICES = [
  { id: 'glm-5.2', label: 'GLM-5.2', default: true, allowed: true, requires_plan: null, efforts: [] },
  {
    id: 'deepseek-flash',
    label: 'DeepSeek',
    default: false,
    allowed: true,
    requires_plan: null,
    efforts: ['low', 'high'],
  },
  {
    id: 'sonnet',
    label: 'Claude Sonnet 5',
    default: false,
    allowed: false,
    requires_plan: 'Reserve',
    efforts: ['low', 'medium', 'high', 'max'],
  },
]

vi.mock('../../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api')>()
  return {
    ...actual,
    getProjectDefaultModel: (...a: unknown[]) => getProjectDefaultModel(...a),
    createProjectAgent: (...a: unknown[]) => createProjectAgent(...a),
    updateProjectAgent: (...a: unknown[]) => updateProjectAgent(...a),
    updateAgentType: (...a: unknown[]) => updateAgentType(...a),
    createAgentType: (...a: unknown[]) => createAgentType(...a),
    setProjectDefaultAgent: (...a: unknown[]) => setProjectDefaultAgent(...a),
  }
})

import { setLocale } from '../../i18n'

import AgentEditorDialog from './AgentEditorDialog.vue'

const PROJECT = 'de808b13-ffd2-4b8a-9d1d-fba7babe389f'

const CUSTOM_TYPE: AgentType = {
  name: 'reviewer',
  title: '代码评审',
  description: '',
  body: '你负责代码评审',
  skills: [],
  mcp_servers: [],
  builtin: false,
}

function mountDialog(agent: ProjectAgent | null, types: AgentType[] = [CUSTOM_TYPE]) {
  return render(AgentEditorDialog, {
    props: { modelValue: true, projectId: PROJECT, agent, types },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

async function clickSave() {
  const dialog = await screen.findByRole('dialog')
  const save = Array.from(dialog.querySelectorAll('button')).find((b) => b.textContent?.trim() === '保存')
  await waitFor(() => expect((save as HTMLButtonElement).disabled).toBe(false))
  await fireEvent.click(save as HTMLButtonElement)
}

function field(label: string): HTMLInputElement {
  return screen.getByLabelText(label, { exact: false }) as HTMLInputElement
}

beforeAll(() => {
  vi.stubGlobal('devicePixelRatio', 1)
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  if (!globalThis.matchMedia) {
    globalThis.matchMedia = (() => ({
      matches: false,
      addEventListener() {},
      removeEventListener() {},
      addListener() {},
      removeListener() {},
      dispatchEvent: () => false,
    })) as unknown as typeof globalThis.matchMedia
  }
  if (!globalThis.visualViewport) {
    Object.defineProperty(globalThis, 'visualViewport', {
      configurable: true,
      value: { width: 1024, height: 768, offsetLeft: 0, offsetTop: 0, addEventListener() {}, removeEventListener() {} },
    })
  }
})

const CONFIG = {
  model: null,
  body: 'Review code',
  skills: [],
  effort: null,
  compact_percent: null,
}

beforeEach(() => {
  setLocale('zh-CN')
  createProjectAgent.mockReset().mockResolvedValue({})
  updateProjectAgent.mockReset().mockResolvedValue({})
  updateAgentType.mockReset().mockResolvedValue(CUSTOM_TYPE)
  createAgentType.mockReset()
  setProjectDefaultAgent.mockReset().mockResolvedValue({})
  getProjectDefaultModel.mockReset().mockResolvedValue({ choices: CHOICES, can_manage: true })
})

async function chooseModel(name: string) {
  const boxes = await screen.findAllByRole('combobox')
  await fireEvent.mouseDown(boxes[boxes.length - 1]!)
  await fireEvent.click(await screen.findByRole('option', { name: new RegExp(name) }))
}

function effortButton(name: string): HTMLButtonElement {
  return screen.getByRole('radio', { name }) as HTMLButtonElement
}

afterEach(() => cleanup())

describe('新建时的校验', () => {
  it('一进来不先骂人', async () => {
    mountDialog(null)
    expect(screen.queryByText('填写名字')).toBeNull()
  })

  it('名字空着就不发请求，并当场说明', async () => {
    mountDialog(null)
    await clickSave()
    expect(await screen.findByText('填写名字')).toBeTruthy()
    expect(createProjectAgent).not.toHaveBeenCalled()
  })

  it('标识写成大写或带空格会被拦下', async () => {
    mountDialog(null)
    await fireEvent.update(field('名字'), '代码评审')
    await fireEvent.update(field('标识'), 'Code Reviewer')
    await clickSave()
    expect(await screen.findByText(/只能用小写字母、数字/)).toBeTruthy()
    expect(createProjectAgent).not.toHaveBeenCalled()
  })

  it('填对了就带着去掉首尾空格的值提交', async () => {
    mountDialog(null)
    await fireEvent.update(field('名字'), '  代码评审  ')
    await fireEvent.update(field('标识'), 'reviewer-2')
    await clickSave()
    await waitFor(() => {
      expect(createProjectAgent).toHaveBeenCalledWith(PROJECT, {
        display_name: '代码评审',
        handle: 'reviewer-2',
        type_name: null,
        configuration: { ...CONFIG, body: '' },
      })
    })
  })

  it('标识留空就不传，交给后端生成', async () => {
    mountDialog(null)
    await fireEvent.update(field('名字'), '代码评审')
    await clickSave()
    await waitFor(() => {
      expect(createProjectAgent).toHaveBeenCalledWith(PROJECT, {
        display_name: '代码评审',
        handle: undefined,
        type_name: null,
        configuration: { ...CONFIG, body: '' },
      })
    })
  })

  it('follows the project main model until another is chosen', async () => {
    mountDialog(null)
    await fireEvent.update(field('名字'), '代码评审')
    expect(await screen.findByText('跟随项目主模型（GLM-5.2）')).toBeTruthy()
    await clickSave()
    await waitFor(() => expect(createProjectAgent).toHaveBeenCalled())
    const [, payload] = createProjectAgent.mock.calls[0] as [string, { configuration: object }]
    expect(payload.configuration).toMatchObject({ model: null, effort: null })
  })

  it('saves an available model and an effort it honours', async () => {
    mountDialog(null)
    await fireEvent.update(field('名字'), 'Spark')
    await chooseModel('DeepSeek')
    await waitFor(() => expect(effortButton('高').disabled).toBe(false))
    expect(effortButton('中').disabled).toBe(true)
    await fireEvent.click(effortButton('高'))
    await clickSave()
    await waitFor(() =>
      expect(createProjectAgent).toHaveBeenCalledWith(
        PROJECT,
        expect.objectContaining({
          configuration: expect.objectContaining({ model: 'deepseek-flash', effort: 'high' }),
        })
      )
    )
  })

  it('offers no effort on a model that takes none', async () => {
    mountDialog(null)
    await waitFor(() => expect(screen.getByText(/这个模型不支持调思考强度/)).toBeTruthy())
    expect(effortButton('低').disabled).toBe(true)
    expect(effortButton('高').disabled).toBe(true)
  })

  it('does not let a model the plan leaves out be chosen', async () => {
    mountDialog(null)
    await fireEvent.update(field('名字'), 'Spark')
    const boxes = await screen.findAllByRole('combobox')
    await fireEvent.mouseDown(boxes[boxes.length - 1]!)
    const option = await screen.findByRole('option', { name: /Claude Sonnet 5/ })
    expect(option.textContent).toContain('Reserve')
    await fireEvent.click(option)
    await clickSave()
    await waitFor(() => expect(createProjectAgent).toHaveBeenCalled())
    const [, payload] = createProjectAgent.mock.calls[0] as [string, { configuration: { model: unknown } }]
    expect(payload.configuration.model).not.toBe('sonnet')
  })
})

describe('高级设置', () => {
  async function openAdvanced() {
    await fireEvent.click(await screen.findByRole('button', { name: /高级设置/ }))
  }

  it('收在折叠里，展开后可以设整理阈值', async () => {
    mountDialog(null)
    await fireEvent.update(field('名字'), 'Spark')
    expect(screen.queryByText('自动整理上下文')).toBeNull()
    await openAdvanced()
    await fireEvent.input(await screen.findByLabelText(/自己设定何时整理/), { target: { checked: true } })
    expect(await screen.findByText(/上下文用到 80% 时整理/)).toBeTruthy()
    await clickSave()
    await waitFor(() =>
      expect(createProjectAgent).toHaveBeenCalledWith(
        PROJECT,
        expect.objectContaining({ configuration: expect.objectContaining({ compact_percent: 80 }) })
      )
    )
  })

  it('不是负责人时只读，并说明谁能改', async () => {
    getProjectDefaultModel.mockResolvedValue({ choices: CHOICES, can_manage: false })
    mountDialog(null)
    await openAdvanced()
    expect(await screen.findByText('只有项目所有者或团队管理员能改高级设置')).toBeTruthy()
    expect((screen.getByLabelText(/自己设定何时整理/) as HTMLInputElement).disabled).toBe(true)
  })
})

describe('修改时', () => {
  const existing: ProjectAgent = {
    id: 'a2',
    configuration: CONFIG,
    project_id: PROJECT,
    handle: 'reviewer',
    type_name: 'reviewer',
    display_name: '代码评审',
    seat_handle: 'cheese-a2',
    is_default: false,
    is_active: true,
  }

  it('只改名字时不去动那个可能被别处共用的类型', async () => {
    mountDialog(existing)
    await fireEvent.update(field('名字'), '严格评审')
    await clickSave()
    await waitFor(() => {
      expect(updateProjectAgent).toHaveBeenCalledWith(PROJECT, 'a2', {
        display_name: '严格评审',
        configuration: CONFIG,
      })
    })
    expect(updateAgentType).not.toHaveBeenCalled()
  })

  it('saves role edits only on the selected agent', async () => {
    mountDialog(existing)
    await fireEvent.update(field('角色设定'), 'Check security')
    await clickSave()
    await waitFor(() =>
      expect(updateProjectAgent).toHaveBeenCalledWith(PROJECT, 'a2', {
        configuration: { ...CONFIG, body: 'Check security' },
      })
    )
    expect(updateAgentType).not.toHaveBeenCalled()
    expect(existing.configuration.body).toBe('Review code')
  })

  it('leaves a teammate nobody named unnamed when only its role changes', async () => {
    mountDialog({ ...existing, display_name: '芝士', name_source: 'default' })
    expect((field('名字') as HTMLInputElement).value).toBe('芝士')
    await fireEvent.update(field('角色设定'), 'Check security')
    await clickSave()
    await waitFor(() =>
      expect(updateProjectAgent).toHaveBeenCalledWith(PROJECT, 'a2', {
        configuration: { ...CONFIG, body: 'Check security' },
      })
    )
  })

  it('edits agents created from a built-in preset without editing the preset', async () => {
    mountDialog({ ...existing, type_name: 'fullstack-engineer' }, [
      { ...CUSTOM_TYPE, name: 'fullstack-engineer', builtin: true },
    ])
    expect(field('角色设定').readOnly).toBe(false)
    expect(screen.queryByText('平台预设不能改')).toBeNull()
    await clickSave()
    await waitFor(() => expect(updateProjectAgent).toHaveBeenCalled())
    expect(createAgentType).not.toHaveBeenCalled()
  })
})
