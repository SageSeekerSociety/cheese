// 「默认队友」那一格（#944）。两处表单共用它，所以这里量的是它出什么：
//   1. 选一个平台预设 → 报出它的 `name`（提交给接口的就是这个名字）；
//   2. 选「继承」→ 报出空串（由调用方按 `defaultRolePayload` 决定带不带这个键）；
//   3. 预设清单读不到时不崩，也不把整格藏掉 —— 只留「继承」那一项。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor, within } from '@testing-library/vue'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('vue-i18n', async () => {
  const actual = await vi.importActual<typeof import('vue-i18n')>('vue-i18n')
  return { ...actual, useI18n: () => ({ t: (key: string) => key }) }
})

const listAgentTypes = vi.fn()
vi.mock('@/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api')>()
  return { ...actual, listAgentTypes: (...a: unknown[]) => listAgentTypes(...a) }
})

import DefaultTeammatePicker from './DefaultTeammatePicker.vue'

const PRESETS = {
  data: [
    {
      name: 'academic-research',
      title: '学术研究',
      description: '查资料、做综述',
      body: '',
      skills: ['documents', 'wolfram'],
      mcp_servers: [],
      builtin: true,
    },
    {
      name: 'coding-tutor',
      title: '编程陪练',
      description: '',
      body: '',
      skills: [],
      mcp_servers: [],
      builtin: true,
    },
  ],
  total: 2,
}

let picked: string | null = null

function mount(modelValue = '', rawValue: string | null = null) {
  picked = null
  return render(DefaultTeammatePicker, {
    props: { modelValue, rawValue, 'onUpdate:modelValue': (value: string) => (picked = value) },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

/** 点开这一格的清单：选项画在浮层里，查询挂在 `document.body` 上找得到。 */
async function openMenu(view: ReturnType<typeof mount>) {
  await fireEvent.mouseDown(within(view.getByTestId('default-teammate')).getByRole('combobox'))
}

// Vuetify 的浮层摆放要读这几样，happy-dom 里没有（少一个就是一条未处理的 rejection）。
beforeAll(() => {
  vi.stubGlobal('devicePixelRatio', 1)
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    pageLeft: 0,
    pageTop: 0,
    scale: 1,
    addEventListener() {},
    removeEventListener() {},
  })
})

afterAll(() => vi.unstubAllGlobals())

beforeEach(() => {
  listAgentTypes.mockReset().mockResolvedValue(PRESETS)
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('DefaultTeammatePicker', () => {
  it('选一个预设：报出它的 name', async () => {
    const view = mount('')
    await openMenu(view)

    await fireEvent.click(await view.findByRole('option', { name: /学术研究/ }))
    await waitFor(() => expect(picked).toBe('academic-research'))
  })

  it('选「继承」：报出空串', async () => {
    const view = mount('academic-research')
    await openMenu(view)

    await fireEvent.click(await view.findByRole('option', { name: /defaultTeammate\.inherit/ }))
    await waitFor(() => expect(picked).toBe(''))
  })

  it('预设清单读不到：只留「继承」，不崩也不把整格藏掉', async () => {
    listAgentTypes.mockRejectedValue(new Error('boom'))
    const view = mount('')
    await openMenu(view)

    expect(await view.findByRole('option', { name: /defaultTeammate\.inherit/ })).toBeTruthy()
    expect(view.queryByRole('option', { name: /学术研究/ })).toBeNull()
    expect(picked).toBeNull()
  })

  it('本来写过、现在选回「继承」：说一句保存会清掉', () => {
    const view = mount('', 'academic-research')
    expect(view.getByTestId('default-teammate-will-clear')).toBeTruthy()
  })
})
