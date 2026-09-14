// 新建 / 修改队友的表单。两件事值得被盯着：
//   1. 名字空着、标识写错，不能一路发到后端再收一个 422 —— 人得当场看见
//   2. 只是改了个名字，不能顺手把一个被别的项目共用的类型也重写一遍
import type { AgentType, ProjectAgent } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const createProjectAgent = vi.fn()
const updateProjectAgent = vi.fn()
const getProjectAgentOptions = vi.fn()
const updateAgentType = vi.fn()
const createAgentType = vi.fn()
const setProjectDefaultAgent = vi.fn()

vi.mock('../../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api')>()
  return {
    ...actual,
    getProjectAgentOptions: () => getProjectAgentOptions(),
    createProjectAgent: (...a: unknown[]) => createProjectAgent(...a),
    updateProjectAgent: (...a: unknown[]) => updateProjectAgent(...a),
    updateAgentType: (...a: unknown[]) => updateAgentType(...a),
    createAgentType: (...a: unknown[]) => createAgentType(...a),
    setProjectDefaultAgent: (...a: unknown[]) => setProjectDefaultAgent(...a),
  }
})

import AgentEditorDialog from './AgentEditorDialog.vue'

const PROJECT = 'de808b13-ffd2-4b8a-9d1d-fba7babe389f'

const CUSTOM_TYPE: AgentType = {
  name: 'reviewer',
  title: '代码评审',
  description: '',
  body: '你负责代码评审',
  skills: [],
  mcp_servers: [],
  model: null,
  effort: null,
  harness: null,
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
  body: 'Review code',
  model: 'sonnet',
  harness: 'claude-code',
  skills: [],
  mcp_servers: [],
  effort: null,
}

beforeEach(() => {
  getProjectAgentOptions.mockReset().mockResolvedValue({
    model: {
      state: 'choosable',
      choices: [
        { id: 'sonnet', label: 'Sonnet', default: true },
        { id: 'opus', label: 'Opus', default: false },
      ],
    },
  })
  createProjectAgent.mockReset().mockResolvedValue({})
  updateProjectAgent.mockReset().mockResolvedValue({})
  updateAgentType.mockReset().mockResolvedValue(CUSTOM_TYPE)
  createAgentType.mockReset()
  setProjectDefaultAgent.mockReset().mockResolvedValue({})
})

afterEach(() => cleanup())

describe('新建时的校验', () => {
  it('selects the harness when changing model and preserves the role', async () => {
    getProjectAgentOptions.mockResolvedValue({
      harness: {
        state: 'choosable',
        choices: [
          { id: 'claude-code', label: 'Claude Code' },
          { id: 'codex', label: 'Codex' },
        ],
      },
      model: {
        state: 'choosable',
        choices: [
          { id: 'sonnet', label: 'Sonnet', default: true, harnesses: ['claude-code'] },
          { id: 'codex-fixture', label: 'Codex fixture', harnesses: ['codex'] },
        ],
      },
    })
    mountDialog(null)
    await fireEvent.update(field('名字'), '代码评审')
    await fireEvent.update(field('角色设定'), 'Review code')
    expect(screen.queryByLabelText('运行方式', { exact: false })).toBeNull()
    await waitFor(() => expect(field('模型').disabled).toBe(false))
    await fireEvent.mouseDown(field('模型'))
    await fireEvent.click(await screen.findByText('Codex fixture', { selector: '.v-list-item-title' }))
    await clickSave()
    await waitFor(() =>
      expect(createProjectAgent).toHaveBeenCalledWith(
        PROJECT,
        expect.objectContaining({
          configuration: { ...CONFIG, model: 'codex-fixture', harness: 'codex' },
        })
      )
    )
  })

  it('一进来不先骂人', async () => {
    mountDialog(null)
    expect(screen.queryByText('请填写名字')).toBeNull()
  })

  it('名字空着就不发请求，并当场说明', async () => {
    mountDialog(null)
    await clickSave()
    expect(await screen.findByText('请填写名字')).toBeTruthy()
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
})

describe('修改时', () => {
  const existing: ProjectAgent = {
    id: 'a2',
    configuration: CONFIG,
    project_id: PROJECT,
    handle: 'reviewer',
    type_name: 'reviewer',
    display_name: '代码评审',
    is_default: false,
    configured: true,
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
        display_name: existing.display_name,
        configuration: { ...CONFIG, body: 'Check security' },
      })
    )
    expect(updateAgentType).not.toHaveBeenCalled()
    expect(existing.configuration.body).toBe('Review code')
  })

  it.each(['claude-code', 'codex'])('preserves an API model using %s when only renaming', async (harness) => {
    getProjectAgentOptions.mockResolvedValue({
      model: {
        state: 'choosable',
        choices: [{ id: 'api-model', label: 'API model', harnesses: ['claude-code', 'codex'] }],
      },
    })
    const configuration = { ...CONFIG, model: 'api-model', harness }
    mountDialog({ ...existing, configuration })
    await fireEvent.update(field('名字'), 'New name')
    await clickSave()
    await waitFor(() =>
      expect(updateProjectAgent).toHaveBeenCalledWith(PROJECT, existing.id, {
        display_name: 'New name',
        configuration,
      })
    )
  })

  it('saves the selected model without changing the original draft source', async () => {
    mountDialog(existing)
    await waitFor(() => expect(field('模型').disabled).toBe(false))
    await fireEvent.mouseDown(field('模型'))
    await fireEvent.click(await screen.findByText('Opus', { selector: '.v-list-item-title' }))
    await clickSave()
    await waitFor(() =>
      expect(updateProjectAgent).toHaveBeenCalledWith(PROJECT, existing.id, {
        display_name: existing.display_name,
        configuration: { ...CONFIG, model: 'opus' },
      })
    )
    expect(existing.configuration.model).toBe('sonnet')
  })

  it('keeps an unavailable saved model visible and refuses a silent replacement', async () => {
    mountDialog({ ...existing, configuration: { ...CONFIG, model: 'unavailable-model' } })
    await clickSave()
    expect(await screen.findByText('请选择当前项目可用的模型')).toBeTruthy()
    expect(updateProjectAgent).not.toHaveBeenCalled()
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
