// 「参考资料」选择器（#944）：从资料库里勾，不再手填编号。这里量三条规矩：
//   1. 候选只有「所有成员」那一档 —— 「仅管理员」的课件学生读不到，也不该出现在老师的名单里；
//   2. 勾一下只动这一项，已有编号的顺序照旧（新勾的接在末尾）；
//   3. 已经不在清单里的编号**不静默丢掉** —— 单列出来，等人自己去掉。
import type { SpaceMaterial } from '@/types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('vue-i18n', async () => {
  const actual = await vi.importActual<typeof import('vue-i18n')>('vue-i18n')
  return {
    ...actual,
    useI18n: () => ({
      t: (key: string, named?: Record<string, unknown>) => (named ? `${key}:${JSON.stringify(named)}` : key),
    }),
  }
})

import TeachingMaterialPicker from './TeachingMaterialPicker.vue'

function material(id: number, name: string, visibility: 'members' | 'admins'): SpaceMaterial {
  return {
    id,
    name,
    visibility,
    type: 'file',
    size: null,
    mime: null,
    uploaderId: null,
    createdAt: 0,
    downloadCount: 0,
  }
}

const MATERIALS = [
  material(161, '第03讲-红黑树.pdf', 'members'),
  material(162, '参考答案-红黑树.pdf', 'admins'),
  material(163, '测试用例与脚手架.zip', 'members'),
]

let picked: number[] = []

function mount(modelValue: number[], materials: SpaceMaterial[] = MATERIALS) {
  picked = modelValue
  const view = render(TeachingMaterialPicker, {
    props: {
      modelValue,
      materials,
      'onUpdate:modelValue': (next: number[]) => (picked = next),
    },
    global: { plugins: [createVuetify({ components, directives })] },
  })
  return view
}

// Vuetify 的勾选框绑的是 input 的 `input` 事件（`e.target.checked`），不是 click：
// jsdom 里点它既不改 checked 也不派发事件，只能自己置好 checked 再派 input。
async function toggleRow(view: ReturnType<typeof mount>, label: string, on: boolean) {
  await fireEvent.input(view.getByLabelText(label), { target: { checked: on } })
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('TeachingMaterialPicker', () => {
  it('只列「所有成员」那一档', () => {
    const view = mount([])

    expect(view.getByLabelText('第03讲-红黑树.pdf')).toBeTruthy()
    expect(view.getByLabelText('测试用例与脚手架.zip')).toBeTruthy()
    expect(view.queryByLabelText('参考答案-红黑树.pdf')).toBeNull()
  })

  it('勾一项只加这一项，已有编号的顺序不动', async () => {
    const view = mount([163])

    await toggleRow(view, '第03讲-红黑树.pdf', true)
    expect(picked).toEqual([163, 161])
  })

  it('取消勾选只去掉那一项', async () => {
    const view = mount([161, 163])

    await toggleRow(view, '第03讲-红黑树.pdf', false)
    expect(picked).toEqual([163])
  })

  it('清单里没有的编号不静默丢掉：单列出来，可以手动去掉', async () => {
    const view = mount([999, 161])

    const dangling = view.getByTestId('teaching-materials-dangling')
    expect(dangling.textContent).toContain('999')

    await fireEvent.click(view.getByText('spaces.teaching.fields.materialsDanglingDrop:{"id":999}'))
    expect(picked).toEqual([161])
  })

  it('资料库空着时说的是「还没有文件」，不是一片空白', () => {
    const view = mount([], [])

    expect(view.getByTestId('teaching-materials-empty')).toBeTruthy()
    expect(view.queryByTestId('teaching-materials')).toBeNull()
  })
})
