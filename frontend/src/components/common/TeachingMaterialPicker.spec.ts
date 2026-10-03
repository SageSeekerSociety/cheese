// 「参考资料」选择器（#944）：从资料库里勾，不再手填编号。这里量五条规矩：
//   1. 勾中的课件摆在外面的 chip 上，点 chip 上的 ✕ 去掉；清单默认收起，点那一栏才摊开；
//   2. 候选只有「所有成员」那一档 —— 「仅管理员」的课件学生读不到，也不该出现在老师的名单里；
//   3. 勾一下只动这一项，已有编号的顺序照旧（新勾的接在末尾）；
//   4. 引用到的编号**选不到**了（删掉，或者事后被改成「仅管理员」）就不静默丢掉 ——
//      单列出来，等人自己去掉；
//   5. 清单**读不出来**时不判失效：一次网络失败不该让人删掉有效的引用。
import type { SpaceMaterial, SpaceMaterialsState } from '@/types'

import { defineComponent } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
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
      t: (key: string, named?: Record<string, unknown>) => (named ? `${key} ${JSON.stringify(named)}` : key),
    }),
  }
})

import TeachingMaterialPicker from './TeachingMaterialPicker.vue'

const Stub = defineComponent({ render: () => null })

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

const LIBRARY_PATH = '/spaces/651/manage/settings/materials'

let picked: number[] = []

function mount(
  modelValue: number[],
  materials: SpaceMaterial[] = MATERIALS,
  state: SpaceMaterialsState = 'ready',
  libraryTo?: string
) {
  picked = modelValue
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/:pathMatch(.*)*', component: Stub }],
  })
  const view = render(TeachingMaterialPicker, {
    props: {
      modelValue,
      materials,
      state,
      libraryTo,
      'onUpdate:modelValue': (next: number[]) => (picked = next),
    },
    global: { plugins: [createVuetify({ components, directives }), router] },
  })
  return view
}

/** 摊开资料库清单。它默认收起，勾选框点开才在。 */
async function expand(view: ReturnType<typeof mount>) {
  await fireEvent.click(view.getByTestId('teaching-materials-toggle'))
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
  it('清单默认收起：点那一栏才摊开', async () => {
    const view = mount([])

    expect(view.getByTestId('teaching-materials-toggle')).toBeTruthy()
    expect(view.queryByTestId('teaching-materials')).toBeNull()

    await expand(view)
    expect(view.getByTestId('teaching-materials')).toBeTruthy()
  })

  it('只列「所有成员」那一档', async () => {
    const view = mount([])
    await expand(view)

    expect(view.getByLabelText('第03讲-红黑树.pdf')).toBeTruthy()
    expect(view.getByLabelText('测试用例与脚手架.zip')).toBeTruthy()
    expect(view.queryByLabelText('参考答案-红黑树.pdf')).toBeNull()
  })

  it('勾中的课件摆在外面：收起时也看得见带了哪几份', async () => {
    const view = mount([161])

    expect(view.getByTestId('teaching-material-chip').textContent).toContain('第03讲-红黑树.pdf')
  })

  it('点 chip 上的 ✕ 去掉那一项，不用先摊开清单', async () => {
    const view = mount([161, 163])

    await fireEvent.click(view.getByTestId('teaching-material-chip-remove-161'))
    expect(picked).toEqual([163])
  })

  it('chip 上的 ✕ 不把清单开合状态改掉', async () => {
    const view = mount([161])

    await fireEvent.click(view.getByTestId('teaching-material-chip-remove-161'))
    expect(view.queryByTestId('teaching-materials')).toBeNull()
  })

  it('chip 上的 ✕ 键盘也按得动：那颗 chip 不把 Enter/Space 抢走', () => {
    const view = mount([161])
    const x = view.getByTestId('teaching-material-chip-remove-161')

    // v-chip 因为绑了 click 会给根节点挂一个 keydown 处理器，Enter/Space 上一律
    // `preventDefault`；那颗 ✕ 是它的子孙，键事件冒上去就被取消掉，键盘用户按它
    // 什么也不会发生。按钮上的 `.stop` 就是挡这个 —— 这里量的是它没被取消。
    for (const key of ['Enter', ' ']) {
      const press = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true })
      x.dispatchEvent(press)
      expect(press.defaultPrevented).toBe(false)
    }
  })

  it('换一块板（重新取数）时清单收回去，不带着上次的展开状态回来', async () => {
    const view = mount([])
    await expand(view)
    expect(view.getByTestId('teaching-materials')).toBeTruthy()

    await view.rerender({ modelValue: [], materials: [], state: 'loading' })
    await view.rerender({ modelValue: [], materials: MATERIALS, state: 'ready' })

    expect(view.queryByTestId('teaching-materials')).toBeNull()
  })

  it('chip 按勾的顺序摆，不按清单顺序', () => {
    const view = mount([163, 161])

    const names = view.getAllByTestId('teaching-material-chip').map((chip) => chip.textContent)
    expect(names[0]).toContain('测试用例与脚手架.zip')
    expect(names[1]).toContain('第03讲-红黑树.pdf')
  })

  it('勾一项只加这一项，已有编号的顺序不动', async () => {
    const view = mount([163])
    await expand(view)

    await toggleRow(view, '第03讲-红黑树.pdf', true)
    expect(picked).toEqual([163, 161])
  })

  it('取消勾选只去掉那一项', async () => {
    const view = mount([161, 163])
    await expand(view)

    await toggleRow(view, '第03讲-红黑树.pdf', false)
    expect(picked).toEqual([163])
  })

  it('引用到的编号已经不在清单里：单列出来，可以手动去掉', async () => {
    const view = mount([999, 161])

    const dangling = view.getByTestId('teaching-materials-dangling')
    expect(dangling.textContent).toContain('999')

    await fireEvent.click(view.getByText('spaces.teaching.fields.materialsDanglingDrop {"id":999}'))
    expect(picked).toEqual([161])
  })

  it('引用到的课件被改成了「仅管理员」：也算失效，列出来让人去掉', async () => {
    // 162 还在清单里，但已经变成成员读不到的那一档 —— 它不在候选里，也不能算「还在」。
    const view = mount([162])

    const dangling = view.getByTestId('teaching-materials-dangling')
    expect(dangling.textContent).toContain('162')

    await fireEvent.click(view.getByText('spaces.teaching.fields.materialsDanglingDrop {"id":162}'))
    expect(picked).toEqual([])
  })

  it('清单读不出来时什么都不判：不列候选，也不说谁失效了', () => {
    const view = mount([161, 999], [], 'error')

    expect(view.getByTestId('teaching-materials-error')).toBeTruthy()
    expect(view.queryByTestId('teaching-materials-toggle')).toBeNull()
    expect(view.queryByTestId('teaching-materials-dangling')).toBeNull()
  })

  it('资料库空着时说的是「还没有文件」，不是一片空白', async () => {
    const view = mount([], [])
    await expand(view)

    expect(view.getByTestId('teaching-materials-empty')).toBeTruthy()
    expect(view.queryByTestId('teaching-materials')).toBeNull()
  })

  it('给出去资料库那一页的地址，清单底下才有「上传到资料库」', async () => {
    const bare = mount([])
    await expand(bare)
    expect(bare.queryByTestId('teaching-materials-upload')).toBeNull()

    cleanup()

    const linked = mount([], MATERIALS, 'ready', LIBRARY_PATH)
    await expand(linked)
    const upload = linked.getByTestId('teaching-materials-upload')
    expect(upload.getAttribute('href')).toBe('/spaces/651/manage/settings/materials')
  })
})
