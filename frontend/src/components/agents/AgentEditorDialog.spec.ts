// 新建 / 修改队友的表单。三件事值得被盯着：
//   1. 名字空着、标识写错，不能一路发到后端再收一个 422 —— 人得当场看见
//   2. 只是改了个名字，不能顺手把一个被别的项目共用的类型也重写一遍
//   3. 存下去的只有角色 —— 模型和运行方式不在这张表单上，也不在它发出去的
//      payload 里（模型绑在活上，运行方式是部署的开发者选项）
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

vi.mock('../../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api')>()
  return {
    ...actual,
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
  skills: [],
  mcp_servers: [],
}

beforeEach(() => {
  createProjectAgent.mockReset().mockResolvedValue({})
  updateProjectAgent.mockReset().mockResolvedValue({})
  updateAgentType.mockReset().mockResolvedValue(CUSTOM_TYPE)
  createAgentType.mockReset()
  setProjectDefaultAgent.mockReset().mockResolvedValue({})
})

afterEach(() => cleanup())

describe('新建时的校验', () => {
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

  // 存下去的是一个角色。模型和运行方式不在这张表单上 —— 也就不该从这里被写进
  // 任何一行 configuration：这两样各有自己的归处（活、部署），从两个地方都能
  // 设的东西，人最后看到的是哪一个说了算就没人答得上来了。
  it('新建时既不显示也不提交模型和运行方式', async () => {
    mountDialog(null)
    expect(screen.queryByLabelText('模型')).toBeNull()
    expect(screen.queryByLabelText('运行方式')).toBeNull()
    await fireEvent.update(field('名字'), '代码评审')
    await clickSave()
    await waitFor(() => expect(createProjectAgent).toHaveBeenCalled())
    const [, payload] = createProjectAgent.mock.calls[0] as [string, { configuration: object }]
    expect(Object.keys(payload.configuration).sort()).toEqual(['body', 'mcp_servers', 'skills'])
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
        display_name: existing.display_name,
        configuration: { ...CONFIG, body: 'Check security' },
      })
    )
    expect(updateAgentType).not.toHaveBeenCalled()
    expect(existing.configuration.body).toBe('Review code')
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
