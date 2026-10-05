/**
 * 知识库页拆开之前先钉住的那几块。
 *
 * `views/teams/detail/Knowledge.vue` 1508 行，要拆进一份 composable 和
 * `components/teams/knowledge/` 下面几件。这一份补的是它现在**没有任何 spec**
 * 这件事 —— 页上每一件事（取数、两种视图、筛选、详情、删除、跳转）都只由这一份
 * 盯住，所以拆的时候哪一块被挪丢了，这里当场红。
 *
 * 钉的是这五块：
 *
 *   1. **取数**：打开就按当前团队取一页，参数是这一页自己定的那一套；取不到时
 *      退成空态而不是崩掉，空态那两句话按「有没有筛选」分岔；
 *   2. **筛选**：搜索框、资料类型、标签各写进请求的哪个字段（类型是中文名到
 *      type code 的那张表）；
 *   3. **两种视图**：网格画的是什么、列表画的是什么、列表里那颗删除键按什么
 *      出现（只有自己放上去的才有）；
 *   4. **详情**：点开画的是哪几行资料信息、来源频道与原始讨论；
 *   5. **写入**：删除与「打开资料」分别打到哪儿。
 *
 * 挂的是真页，接的却是**假的 api 模块**（`@/network/api/knowledges` /
 * `materials`）：这一页的整条链子就是「页 → api」，没有 store 夹在中间，假在
 * 网络那一层和假在模块那一层是同一件事，而模块那一层能直接读请求参数。
 *
 * 绿在拆之前的旧文件上；拆完必须原样绿。
 */
import type { Component } from 'vue'
import type { Knowledge, Team, User } from '@/types'

import { ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor, within } from '@testing-library/vue'
import dayjs from 'dayjs'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const listMock = vi.hoisted(() => vi.fn())
const createMock = vi.hoisted(() => vi.fn())
const deleteMock = vi.hoisted(() => vi.fn())
const uploadMock = vi.hoisted(() => vi.fn())
const confirmMock = vi.hoisted(() => vi.fn())
const alertMock = vi.hoisted(() => vi.fn())
const successMock = vi.hoisted(() => vi.fn())
const errorMock = vi.hoisted(() => vi.fn())

vi.mock('@/network/api/knowledges', () => ({
  KnowledgesApi: { create: createMock, list: listMock, deleteKnowledge: deleteMock },
}))
vi.mock('@/network/api/materials', () => ({ MaterialsApi: { upload: uploadMock } }))
vi.mock('vuetify-sonner', () => ({ toast: { success: successMock, error: errorMock } }))

/** 确认框回什么由这一格决定；`answer` 是「人点了确定还是取消」。 */
let answer = true
vi.mock('@/plugins/dialog', async () => ({
  ...(await vi.importActual<typeof import('@/plugins/dialog')>('@/plugins/dialog')),
  useDialog: () => ({
    confirm: confirmMock,
    alert: alertMock,
  }),
}))

/** 编辑器不进 happy-dom：这里测的是传出去什么，不是编辑出来什么。 */
vi.mock('@/components/common/Editor/TipTapEditor.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    default: defineComponent({ name: 'TipTapEditor', setup: () => () => h('div', { class: 'tiptap-stub' }) }),
    __isTeleport: false,
    __isKeepAlive: false,
  }
})

vi.mock('@/services/account', async () => {
  const { ref: make } = await import('vue')
  return { currentUserId: make(1) }
})

import KnowledgePage from './Knowledge.vue'
// 原文，用来钉住「样式写在哪一件里」（`?raw` 由 vite 直接给字符串）。
import pageSource from './Knowledge.vue?raw'

import dialogSource from '@/components/teams/knowledge/KnowledgeDetailDialog.vue?raw'
import gridSource from '@/components/teams/knowledge/KnowledgeGrid.vue?raw'
import { setLocale } from '@/i18n'
import { teamDataInjectionKey } from '@/keys'

const ME = 1
const OTHER = 2

function user(id: number, nickname: string): User {
  return { id, nickname, avatarId: 0 } as unknown as User
}

/** 默认是一条「我放上去的文本」；每一格只覆盖它真正在讲的那几个字段。 */
function knowledge(over: Partial<Knowledge> = {}): Knowledge {
  return {
    id: 1,
    name: '设计规范',
    type: 'TEXT',
    content: JSON.stringify({ richText: { type: 'doc', content: [] } }),
    materialId: undefined,
    material: undefined,
    thumbnail: undefined,
    teamId: 7,
    labels: ['设计', '前端', '规范', '多余'],
    creator: user(ME, '我'),
    sourceChannel: { id: 3, name: '设计频道' },
    createdAt: 1_700_000_000_000,
    updatedAt: 1_700_000_000_000,
    ...over,
  } as Knowledge
}

function page(items: Knowledge[], over: Record<string, unknown> = {}) {
  return {
    data: {
      knowledges: items,
      page: { pageStart: 0, pageSize: 20, hasMore: false, nextStart: null, total: items.length, ...over },
    },
  }
}

/** 三条：一条我的图片资料、一条别人的文本、一条我的链接。 */
function three(): Knowledge[] {
  return [
    knowledge({
      id: 1,
      name: '设计规范',
      type: 'MATERIAL',
      material: {
        id: 11,
        type: 'image',
        url: 'https://cdn.example/img.png',
        meta: { width: 100, height: 80, size: 2048, thumbnail: 'https://cdn.example/thumb.png' },
      },
      thumbnail: 'https://cdn.example/thumb.png',
    }),
    knowledge({ id: 2, name: '会议纪要', type: 'TEXT', creator: user(OTHER, '爱丽丝'), labels: ['会议'] }),
    knowledge({
      id: 3,
      name: '官网',
      type: 'LINK',
      creator: user(ME, '我'),
      content: JSON.stringify({ url: 'https://example.com', title: '官网', description: '首页' }),
      labels: [],
    }),
  ]
}

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
  // 浮层（对话框）定位要读它；happy-dom 里没有这个对象。
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    scale: 1,
    addEventListener() {},
    removeEventListener() {},
  })
})

beforeEach(() => {
  answer = true
  listMock.mockReset().mockResolvedValue(page(three()))
  createMock.mockReset()
  deleteMock.mockReset().mockResolvedValue({ data: undefined })
  uploadMock.mockReset()
  confirmMock.mockReset().mockImplementation(() => ({ wait: () => Promise.resolve(answer) }))
  alertMock.mockReset()
  successMock.mockReset()
  errorMock.mockReset()
  vi.spyOn(console, 'error').mockImplementation(() => {})
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

function mount() {
  setLocale('zh-CN')
  const team = ref({ id: 7, name: 'Cheese 核心组' } as unknown as Team)
  return render(KnowledgePage as unknown as Component, {
    global: {
      plugins: [createVuetify({ components, directives })],
      provide: { [teamDataInjectionKey as symbol]: team },
    },
  })
}

function lastParams() {
  return listMock.mock.calls[listMock.mock.calls.length - 1]![0] as Record<string, unknown>
}

/** 页上那颗搜索框。工具栏在 `loading` 落下来之前不画，所以这里等一等再取。 */
async function searchBox(view: ReturnType<typeof mount>) {
  await waitFor(() => expect(view.container.querySelector('.knowledge-search input')).toBeTruthy())
  return view.container.querySelector('.knowledge-search input') as HTMLInputElement
}

async function selectBox(view: ReturnType<typeof mount>, cls: string) {
  await waitFor(() => expect(view.container.querySelector(`${cls} input`)).toBeTruthy())
  return view.container.querySelector(`${cls} input`) as HTMLInputElement
}

function gridCards(view: ReturnType<typeof mount>) {
  return Array.from(view.container.querySelectorAll('.resource-card'))
}

function tableRows(view: ReturnType<typeof mount>) {
  return Array.from(view.container.querySelectorAll('.resource-row'))
}

async function toListView(view: ReturnType<typeof mount>) {
  await waitFor(() => expect(view.container.querySelectorAll('.v-btn-toggle button').length).toBe(2))
  await fireEvent.click(view.container.querySelectorAll('.v-btn-toggle button')[1]!)
  await waitFor(() => expect(tableRows(view).length).toBeGreaterThan(0))
}

describe('取数', () => {
  it('打开就按当前团队取第一页，排序由这一页自己定', async () => {
    mount()
    await waitFor(() => expect(listMock).toHaveBeenCalledTimes(1))
    expect(lastParams()).toMatchObject({
      teamId: 7,
      pageSize: 20,
      sort_by: 'createdAt',
      sort_order: 'desc',
    })
  })

  it('一条都没有时说的是「还没有内容」，不是「没筛到」', async () => {
    listMock.mockResolvedValue(page([]))
    const view = mount()
    expect(await view.findByText('知识库暂无内容')).toBeTruthy()
    expect(view.container.textContent).toContain('在对话中把有价值的内容加入知识库')
    expect(view.container.textContent).not.toContain('请尝试调整筛选条件')
  })

  // 这一条原来说的是「取数失败退成空态，不是一直转圈」。空态是**替服务端说**了一
  // 句「这个团队一条资料都没有」—— 而它其实什么都没读到。现在失败留在原地：说没
  // 读到、给一条重试的路，空态那句话不再出现。
  it('取数失败留在原地说明并给重试，不是一直转圈、也不是「暂无内容」', async () => {
    listMock.mockRejectedValue(new Error('炸了'))
    const view = mount()

    await waitFor(() => expect(view.container.querySelector('.base-load-error')).toBeTruthy())
    const block = view.container.querySelector('.base-load-error') as HTMLElement
    expect(view.queryByText('知识库暂无内容')).toBeNull()
    expect(view.container.querySelector('.loading-container')).toBeNull()

    await fireEvent.click(within(block).getByRole('button'))
    await waitFor(() => expect(listMock).toHaveBeenCalledTimes(2))
  })

  it('403 说的是「没权限」，并且不给一颗按不动的重试', async () => {
    listMock.mockRejectedValue(Object.assign(new Error('nope'), { status: 403 }))
    const view = mount()

    await waitFor(() => expect(view.container.querySelector('.base-load-error')).toBeTruthy())
    const block = view.container.querySelector('.base-load-error') as HTMLElement
    expect(block.textContent).toContain('你没有权限查看')
    expect(within(block).queryByRole('button')).toBeNull()
    expect(view.queryByText('知识库暂无内容')).toBeNull()
  })
})

describe('筛选', () => {
  it('搜索框里的字进 query 字段，每敲一次重取一页', async () => {
    const view = mount()
    await waitFor(() => expect(listMock).toHaveBeenCalledTimes(1))

    await fireEvent.update(await searchBox(view), '设计')

    await waitFor(() => expect(listMock).toHaveBeenCalledTimes(2))
    expect(lastParams()).toMatchObject({ query: '设计' })
  })

  it('筛了但没筛到，空态说的是「调整筛选条件」', async () => {
    const view = mount()
    await waitFor(() => expect(listMock).toHaveBeenCalledTimes(1))

    listMock.mockResolvedValue(page([]))
    await fireEvent.update(await searchBox(view), '没有的东西')

    await waitFor(() => expect(view.container.textContent).toContain('请尝试调整筛选条件'))
  })

  it('资料类型那颗下拉把中文名翻成 type code', async () => {
    const view = mount()
    await waitFor(() => expect(listMock).toHaveBeenCalledTimes(1))

    await fireEvent.mouseDown(await selectBox(view, '.resource-type-filter'))
    await fireEvent.click(await view.findByRole('option', { name: '链接' }))

    await waitFor(() => expect(listMock).toHaveBeenCalledTimes(2))
    expect(lastParams()).toMatchObject({ type: 'LINK' })
  })

  it('标签那颗下拉写进 labels 数组；选项来自已经取回来的那几条', async () => {
    const view = mount()
    await waitFor(() => expect(listMock).toHaveBeenCalledTimes(1))

    await fireEvent.mouseDown(await selectBox(view, '.tag-filter'))
    await fireEvent.click(await view.findByRole('option', { name: '会议' }))

    await waitFor(() => expect(listMock).toHaveBeenCalledTimes(2))
    expect(lastParams()).toMatchObject({ labels: ['会议'] })
  })
})

describe('两种视图', () => {
  it('网格里每张卡写的是名字、类型名、日期和前三个标签', async () => {
    const view = mount()
    await waitFor(() => expect(gridCards(view).length).toBe(3))

    const first = gridCards(view)[0]!
    expect(first.textContent).toContain('设计规范')
    expect(first.textContent).toContain('图片')
    // 卡片上的日期是这条自己那个时间戳的「月/日」，跟着本地时区走。
    expect(first.textContent).toContain(dayjs(1_700_000_000_000).format('MM/DD'))
    // 四个标签只画三个，剩下一个折成 +1。
    expect(first.textContent).toContain('设计')
    expect(first.textContent).toContain('+1')
    expect(first.textContent).not.toContain('多余')
  })

  it('切到列表画的是表格，每一行还在', async () => {
    const view = mount()
    await waitFor(() => expect(gridCards(view).length).toBe(3))

    await toListView(view)
    expect(tableRows(view).length).toBe(3)
    expect(view.container.querySelector('.resource-table')).toBeTruthy()
  })

  it('删除键只长在自己放上去的那几条上', async () => {
    const view = mount()
    await waitFor(() => expect(gridCards(view).length).toBe(3))
    await toListView(view)

    // 三行：我的图片、别人的文本、我的链接 —— 中间那一行没有删除键。
    const rows = tableRows(view)
    expect(rows[0]!.querySelector('.mdi-delete')).toBeTruthy()
    expect(rows[1]!.querySelector('.mdi-delete')).toBeNull()
    expect(rows[2]!.querySelector('.mdi-delete')).toBeTruthy()
  })
})

describe('详情', () => {
  it('点开一条，资料信息那几行和原始讨论都在', async () => {
    const item = knowledge({
      id: 9,
      name: '会议纪要',
      description: '周会记的东西',
      originalMessage: { content: '把这份发上去', sender: user(OTHER, '爱丽丝'), createdAt: Date.now() },
    })
    listMock.mockResolvedValue(page([item]))
    const view = mount()
    await waitFor(() => expect(gridCards(view).length).toBe(1))

    await fireEvent.click(gridCards(view)[0]!)

    expect(await view.findByText('资料信息')).toBeTruthy()
    // 描述在卡上和详情里各有一处。
    expect((await view.findAllByText('周会记的东西')).length).toBeGreaterThan(0)
    expect(await view.findByText('设计频道')).toBeTruthy()
    expect(await view.findByText('把这份发上去')).toBeTruthy()
  })

  it('没有原始讨论时说的是「没有关联的原始讨论信息」', async () => {
    const view = mount()
    await waitFor(() => expect(gridCards(view).length).toBe(3))

    await fireEvent.click(gridCards(view)[1]!)
    expect(await view.findByText('没有关联的原始讨论信息')).toBeTruthy()
  })
})

describe('写入', () => {
  it('删除：先问一句，确认之后打 api 并把那一行从页上摘掉', async () => {
    const view = mount()
    await waitFor(() => expect(gridCards(view).length).toBe(3))

    // 第一张是我放上去的，详情里才有那颗删除键。
    await fireEvent.click(gridCards(view)[0]!)
    await fireEvent.click(await view.findByText('删除资料'))

    await waitFor(() => expect(deleteMock).toHaveBeenCalledWith(1))
    await waitFor(() => expect(gridCards(view).length).toBe(2))
  })

  it('删除：人点了取消就什么都不发生', async () => {
    answer = false
    const view = mount()
    await waitFor(() => expect(gridCards(view).length).toBe(3))

    await fireEvent.click(gridCards(view)[0]!)
    await fireEvent.click(await view.findByText('删除资料'))

    expect(deleteMock).not.toHaveBeenCalled()
  })

  it('打开资料：链接那条开的是内容里的 url', async () => {
    const open = vi.fn()
    vi.stubGlobal('open', open)

    const view = mount()
    await waitFor(() => expect(gridCards(view).length).toBe(3))

    // 链接那条的卡上那颗「在新窗口打开」。
    const card = gridCards(view)[2]!
    await fireEvent.click(card.querySelector('.mdi-open-in-new')!.closest('button')!)

    expect(open).toHaveBeenCalledWith('https://example.com', '_blank')
  })
})

/**
 * 拆这一页时最容易踩坏的一处：详情对话框是 `v-dialog`，内容被传送到 `body`，
 * 于是**页那一层写不出能命中它的规则**。
 *
 * 拆之前这块样式长在页里，用的是普通作用域规则（`.code-block` 自己带
 * `data-v-页`），所以能命中；拆之后如果照抄成 `:deep(.code-block)`，编译出来是
 * `[data-v-页] .code-block`，而传送之后 body 里没有任何祖先带页的作用域属性 ——
 * 一条都命中不了，底色、字色、等宽字体、`overflow-x` 一起没了（深色那支
 * `:root[data-theme='dark'] :deep(.code-block)` 更绝，作用域会落到 `:root` 上，
 * 永远不匹配）。同一件事实测过：
 *
 *   - 页根作用域 `data-v-bc7d3b32`，`.code-block` 自己带的是 `data-v-f72599cc`
 *     （对话框那一件的）；
 *   - `.code-block` 的祖先链是 .v-overlay → .v-overlay-container → body → html，
 *     一个页作用域都没有，`document.querySelectorAll('[data-v-bc7d3b32] .code-block')`
 *     是 0；
 *   - 把那条祖先条件加上去之后，浅色下算出来的是 happy-dom 的默认底色、
 *     `Times New Roman`、没有 `overflow-x`（也就是「样式全丢」）。
 *
 * 所以这一格钉两件事：**传送到 body 的节点不再有页作用域的祖先**（这是「样式
 * 不能挂在页上」的根据），以及**这两块样式确实长在拥有节点的那一件里**。
 * `.resource-preview` 不在传送链上（网格还在页的子树里），这里一并钉住写法：
 * 两块都不要再由页跨一层去写。
 */
describe('样式归属', () => {
  /** 页根节点身上的作用域属性（`data-v-...`）。 */
  function pageScopes(view: ReturnType<typeof mount>): string[] {
    return (view.container.firstElementChild as Element).getAttributeNames().filter((n) => n.startsWith('data-v-'))
  }

  it('代码块被传送到 body，祖先链上没有页作用域', async () => {
    const item = knowledge({
      id: 9,
      name: '分页查询',
      type: 'CODE',
      content: JSON.stringify({ code: 'const page = await list()', language: 'typescript' }),
    })
    listMock.mockResolvedValue(page([item]))
    const view = mount()
    await waitFor(() => expect(gridCards(view).length).toBe(1))

    await fireEvent.click(gridCards(view)[0]!)
    const block = await waitFor(() => {
      const el = document.body.querySelector('.code-block')
      expect(el).toBeTruthy()
      return el as HTMLElement
    })

    // 它确实被传送到了页的子树外面
    expect(view.container.contains(block)).toBe(false)

    // 它自己带着来源组件的作用域（说明样式该由那一件来写）
    expect(block.getAttributeNames().filter((n) => n.startsWith('data-v-'))).not.toHaveLength(0)

    // 而页的作用域一个都不在它的祖先链上：页写 `[data-v-页] .code-block` 命中不了
    const scopes = pageScopes(view)
    expect(scopes.length).toBeGreaterThan(0)
    const ancestors: Element[] = []
    for (let el = block.parentElement; el; el = el.parentElement) ancestors.push(el)
    scopes.forEach((scope) => {
      expect(ancestors.some((el) => el.hasAttribute(scope))).toBe(false)
      expect(document.body.querySelectorAll(`[${scope}] .code-block`).length).toBe(0)
    })
  })

  it('这两块样式长在拥有节点的那一件里，页上不再有 :deep 版本', () => {
    // 页只是布局；跨一层写给子组件里节点的规则一条都不许回来。
    expect(pageSource).not.toContain(':deep(.code-block)')
    expect(pageSource).not.toContain(':deep(.resource-preview)')
    // 反过来，节点在哪一件，规则就在哪一件。
    expect(dialogSource).toMatch(/\.code-block\s*\{/)
    expect(gridSource).toMatch(/\.resource-preview\s*\{/)
  })
})
