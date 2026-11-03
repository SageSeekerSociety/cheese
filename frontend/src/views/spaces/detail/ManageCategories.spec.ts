// 项目集设置里的「默认队友」那一格（#944）：这里量的是保存那一刻把什么交给了
// `updateCategory` —— 这一格选出来的值必须原样带上（`defaultRolePayload` 的三种口径
// 在 `lib/teaching.spec.ts` 里单独盯着）。
import type { SpaceCategory } from '@/types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor, within } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const updateCategory = vi.fn()
const createCategory = vi.fn()
const fetchCategories = vi.fn()

vi.mock('@/composables/useSpaceData', () => ({
  useSpaceData: () => ({ updateCategory, createCategory, fetchCategories }),
}))

const listAgentTypes = vi.fn()
vi.mock('@/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api')>()
  return { ...actual, listAgentTypes: (...a: unknown[]) => listAgentTypes(...a) }
})

import ManageCategories from './ManageCategories.vue'

import i18n, { setLocale } from '@/i18n'
import { useSpaceStore } from '@/stores/space'

const A_TEAMMATE = {
  name: 'academic-research',
  title: '学术研究',
  description: '',
  body: '',
  skills: ['documents'],
  mcp_servers: [],
  builtin: true,
}

function category(overrides: Partial<SpaceCategory> = {}): SpaceCategory {
  return {
    id: 5,
    name: '基础题',
    description: null,
    displayOrder: 1,
    createdAt: 0,
    updatedAt: 0,
    archivedAt: null,
    defaultRole: null,
    ...overrides,
  }
}

let pinia: ReturnType<typeof createPinia>

beforeAll(() => {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
  vi.stubGlobal('devicePixelRatio', 1)
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
  vi.stubGlobal('matchMedia', () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
    dispatchEvent: () => false,
  }))
  setLocale('zh-CN')
})

afterAll(() => vi.unstubAllGlobals())

function mount(categories: SpaceCategory[]) {
  pinia = createPinia()
  setActivePinia(pinia)
  const store = useSpaceStore()
  store.setCurrentSpaceId(1)
  store.setCategories(categories)
  return render(ManageCategories, {
    global: { plugins: [createVuetify({ components, directives }), pinia, i18n] },
  })
}

beforeEach(() => {
  listAgentTypes.mockReset().mockResolvedValue({ data: [A_TEAMMATE], total: 1 })
  updateCategory.mockReset().mockResolvedValue(undefined)
  createCategory.mockReset().mockResolvedValue(undefined)
  fetchCategories.mockReset().mockResolvedValue(undefined)
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('项目集设置：默认队友', () => {
  it('编辑时选一个预设，保存把它交给 updateCategory', async () => {
    const view = mount([category({ defaultRole: null })])

    await fireEvent.click(view.getByLabelText('编辑分类'))
    const picker = await view.findByTestId('default-teammate')
    // Vuetify 3.13 把 `role=combobox` 同时挂在字段的壳和里面的 `<input>` 上；
    // 带 `aria-label` 的是 `<input>`，按名字找它。
    await fireEvent.mouseDown(within(picker).getByRole('combobox', { name: '默认队友' }))
    await fireEvent.click(await view.findByRole('option', { name: /学术研究/ }))

    await fireEvent.click(view.getByRole('button', { name: '确认' }))
    await waitFor(() => expect(updateCategory).toHaveBeenCalledTimes(1))

    const [id, data] = updateCategory.mock.calls[0] as [number, Record<string, unknown>]
    expect(id).toBe(5)
    expect(data.defaultRole).toBe('academic-research')
  })

  it('本来写过、现在选回「沿用上层设置」：保存交空串，明确清掉这一格', async () => {
    const view = mount([category({ defaultRole: 'academic-research' })])

    await fireEvent.click(view.getByLabelText('编辑分类'))
    const picker = await view.findByTestId('default-teammate')
    await fireEvent.mouseDown(within(picker).getByRole('combobox', { name: '默认队友' }))
    await fireEvent.click(await view.findByRole('option', { name: /沿用上层设置/ }))

    await fireEvent.click(view.getByRole('button', { name: '确认' }))
    await waitFor(() => expect(updateCategory).toHaveBeenCalledTimes(1))

    const [, data] = updateCategory.mock.calls[0] as [number, Record<string, unknown>]
    expect(data.defaultRole).toBe('')
  })
})
