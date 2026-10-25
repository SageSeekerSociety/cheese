// 「默认队友」那一格（#944）。两处表单共用它，所以这里量的是它出什么：
//   1. 选一个平台预设 → 报出它的 `name`（提交给接口的就是这个名字）；
//   2. 选「沿用上层设置」→ 报出空串（由调用方按 `defaultRolePayload` 决定带不带这个键）；
//   3. 预设清单读不到时不崩，也不把整格藏掉 —— 只留「沿用上层设置」那一项。
//
// 预设清单是 props 进来的（取数在调用方，场景规则见 docs/manual/dev/scenes.md），
// 所以这里不发 HTTP：直接把这三种清单交给它。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor, within } from '@testing-library/vue'
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

vi.mock('vue-i18n', async () => {
  const actual = await vi.importActual<typeof import('vue-i18n')>('vue-i18n')
  return { ...actual, useI18n: () => ({ t: (key: string) => key }) }
})

import type { AgentType } from '@/cx_types'

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

function mount(
  modelValue = '',
  rawValue: string | null = null,
  types: AgentType[] = PRESETS.data,
  typesFailed = false,
  inherited?: string | null
) {
  picked = null
  return render(DefaultTeammatePicker, {
    props: {
      modelValue,
      rawValue,
      types,
      typesFailed,
      inherited,
      'onUpdate:modelValue': (value: string) => (picked = value),
    },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

/** 点开这一格的清单：选项画在浮层里，查询挂在 `document.body` 上找得到。
 *
 *  Vuetify 3.13 把 `role=combobox` 同时挂在字段的壳（`.v-field`）和它里面的
 *  `<input>` 上，只按角色找会命中两个；带 `aria-label` 的那个才是 `<input>`，
 *  所以按名字找它（`plugins/vuetify.selectName.spec.ts` 同一口径）。 */
async function openMenu(view: ReturnType<typeof mount>) {
  await fireEvent.mouseDown(
    within(view.getByTestId('default-teammate')).getByRole('combobox', {
      name: 'spaces.teaching.defaultTeammate.title',
    })
  )
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

  it('选「沿用上层设置」：报出空串', async () => {
    const view = mount('academic-research')
    await openMenu(view)

    await fireEvent.click(await view.findByRole('option', { name: /defaultTeammate\.inherit/ }))
    await waitFor(() => expect(picked).toBe(''))
  })

  it('预设清单读不到：只留「沿用上层设置」，不崩也不把整格藏掉', async () => {
    const view = mount('', null, [], true)
    await openMenu(view)

    expect(await view.findByRole('option', { name: /defaultTeammate\.inherit/ })).toBeTruthy()
    expect(view.queryByRole('option', { name: /学术研究/ })).toBeNull()
    expect(picked).toBeNull()
  })

  it('沿用上层设置时写出沿用的是哪一位；上层没设就说没设', () => {
    const view = mount('', null, PRESETS.data, false, 'academic-research')
    expect(view.getByTestId('default-teammate-inherited').textContent).toContain('学术研究')

    cleanup()
    const none = mount('', null, PRESETS.data, false, null)
    expect(none.getByTestId('default-teammate-inherited').textContent).toContain(
      'spaces.teaching.defaultTeammate.inheritNone'
    )
  })

  it('本来写过、现在选回「沿用上层设置」：说一句保存会清掉', () => {
    const view = mount('', 'academic-research')
    expect(view.getByTestId('default-teammate-will-clear')).toBeTruthy()
  })
})
