// 「分类管理」这一页（题目板详情里的一格）：打开它看到的是当前这块板的分类，
// **连归档的一起**（归档的那几条要留在列表里，否则人再也恢复不了它们）。
//
// 钉这一页是因为它的数据全从题目板那边来：这一页自己不认识接口，只在挂载时叫一次
// 取数。取数那一层搬过家（store → composable），搬完之后这一页该发的请求一个都不能
// 少、也不能变 —— 所以这里断言的是「打开就发了那一枪」，不是屏幕。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const listCategories = vi.fn()
const spaceUpdate = vi.fn()

vi.mock('@/network/api/spaces', () => ({
  SpacesApi: {
    listCategories: (...a: unknown[]) => listCategories(...a),
    update: (...a: unknown[]) => spaceUpdate(...a),
  },
}))

vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

vi.mock('vue-i18n', async () => {
  const actual = await vi.importActual<typeof import('vue-i18n')>('vue-i18n')
  return { ...actual, useI18n: () => ({ t: (key: string) => key }) }
})

vi.mock('@/plugins/dialog', () => ({
  useDialog: () => ({ confirm: () => ({ wait: async () => true }), custom: vi.fn() }),
}))

import ManageCategories from '../ManageCategories.vue'

import { useSpaceStore } from '@/stores/space'

const SPACE_ID = 647

const CATEGORIES = [
  { id: 1, name: '平时作业', description: '每周一份', displayOrder: 0, archivedAt: null },
  { id: 2, name: '上学期期末', description: null, displayOrder: 1, archivedAt: 1750000000 },
]

/** 父层（`views/spaces/Detail.vue`）在人走到这一页之前已经把板号放进 store 了。 */
function enterSpace() {
  setActivePinia(createPinia())
  useSpaceStore().currentSpaceId = SPACE_ID
}

/** 页眉（`PageHeader`）要 router，这一页本身不要 —— 钉的是取数，不是页眉，桩掉它。 */
const mountOptions = {
  global: {
    plugins: [createVuetify({ components, directives })],
    stubs: { PageHeader: true },
  },
}

async function mountPage() {
  const utils = render(ManageCategories, mountOptions)
  await waitFor(() => expect(listCategories).toHaveBeenCalled())
  return utils
}

beforeEach(() => {
  vi.spyOn(console, 'error').mockImplementation(() => {})
  listCategories.mockReset().mockImplementation(async () => ({ data: { categories: CATEGORIES } }))
  spaceUpdate.mockReset().mockImplementation(async () => ({ data: { space: {} } }))
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('分类管理', () => {
  it('打开就列一次当前这块板的分类，归档的也要一起列出来', async () => {
    enterSpace()

    await mountPage()

    expect(listCategories).toHaveBeenCalledWith(SPACE_ID, { includeArchived: true })
  })

  it('列回来的分类一条不少地画出来，归档那条也还在', async () => {
    enterSpace()

    const { container } = await mountPage()

    await waitFor(() => {
      expect(container.textContent).toContain('平时作业')
      expect(container.textContent).toContain('上学期期末')
    })
  })

  it('还没有板号的时候一个请求都不发（页面被挂早了也不炸）', async () => {
    setActivePinia(createPinia())

    render(ManageCategories, mountOptions)
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(listCategories).not.toHaveBeenCalled()
  })
})
