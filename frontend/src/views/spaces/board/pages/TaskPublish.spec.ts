// 发题页头上那颗「手写一道 / 从 PDF 生成」的切换（第六批）。
//
// 量的是两件事，都是**跑起来才看得见**的：
//
// - **浏览器实际收到什么**：`POST /tasks/publish/from-pdf/preview|confirm` 那条请求
//   里带的到底是什么（文件、上限、勾中的草稿、改过的文字、出处标记），以及
//   「不该发的请求有没有发出去」（超限的 PDF 一个请求都不该发）。
// - **用户看到什么**：结果区摆的三件事、草稿能不能就地改、确认之后有没有跳走、
//   回执里那两个去处在不在、指向哪一棵树。
//
// 不量「实现长什么样」：不查组件名、不查 state 变量、不查渲染树里有几个节点。
import type { Component } from 'vue'

import { h, nextTick } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import TaskPublish from './TaskPublish.vue'

const previewFromPdf = vi.fn()
const confirmFromPdf = vi.fn()
const spaceDetail = vi.fn()
const listCategories = vi.fn()

vi.mock('@/network/api/tasks', () => ({
  TasksApi: {
    previewFromPdf: (...a: unknown[]) => previewFromPdf(...a),
    confirmFromPdf: (...a: unknown[]) => confirmFromPdf(...a),
  },
}))

vi.mock('@/network/api/spaces', () => ({
  SpacesApi: {
    detail: (...a: unknown[]) => spaceDetail(...a),
    listCategories: (...a: unknown[]) => listCategories(...a),
  },
}))

vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

// 底下那一页（老发题页）整个换掉：这一份测的是新外壳那一层，不是它裹着的那页。
// 「裹得住、接缝接对了」在 `wrappers.spec.ts` 里另有一份。
vi.mock('@/views/spaces/detail/PublishTask.vue', async () => {
  const { defineComponent: dc, h: hh } = await import('vue')
  return { default: dc({ name: 'WriteProbe', setup: () => () => hh('div', { 'data-testid': 'write-probe' }) }) }
})

const SPACE_ID = 7

const stub = { render: () => h('div') }

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/spaces/:spaceId/board/publish', name: 'SpaceBoardTaskPublish', component: stub },
      { path: '/spaces/:spaceId/board/mine', name: 'SpaceBoardMine', component: stub },
      { path: '/spaces/:spaceId/board/review', name: 'SpaceBoardReview', component: stub },
      { path: '/spaces/:spaceId/board/tasks/:taskId', name: 'SpaceBoardTaskDetail', component: stub },
    ],
  })
}

async function mount(component: Component = TaskPublish) {
  const router = makeRouter()
  await router.push(`/spaces/${SPACE_ID}/board/publish`)
  await router.isReady()
  const view = render(component, {
    global: { plugins: [createVuetify({ components, directives }), router, createPinia()] },
  })
  // 那一页要等空间装好才动手，所以每条用例都从「装好了」开始。
  await waitFor(() => expect(view.container.querySelector('[data-testid="write-probe"]')).not.toBeNull())
  return { ...view, router }
}

/** 从挂载结果里读一个 testid 的文字 —— 断言走这只小手，免得满篇 querySelector。 */
function textOf(container: Element, testId: string): string | null {
  return container.querySelector(`[data-testid="${testId}"]`)?.textContent?.replace(/\s+/g, ' ').trim() ?? null
}

/** 那一页的头部那两颗态。 */
async function switchToPdf(view: ReturnType<typeof render> & { router: unknown }) {
  await fireEvent.click(view.getByRole('button', { name: '从 PDF 生成' }))
}

/** 勾 / 取消勾一条草稿。走**原生 click**：`fireEvent.click` 派发的是合成事件，
 *  勾选框的 `checked` 不会跟着翻，Vuetify 那次 `onInput` 读到的就还是旧值。 */
async function toggle(input: HTMLElement) {
  input.click()
  await nextTick()
}

/** 选中一份文件 —— Vuetify 的 `v-file-input` 读的是 `e.target.files`。 */
async function pick(input: HTMLInputElement, file: File) {
  Object.defineProperty(input, 'files', { value: [file], configurable: true })
  await fireEvent.change(input)
}

function pdfFile(name = '计算机系统基础-第五次作业.pdf', bytes = 1024): File {
  return new File([new Uint8Array(bytes)], name, { type: 'application/pdf' })
}

/** 真接口那一版返回：三样东西 + 每条草稿那几项（`PdfTaskDraftData`）。
 *  每次现造一份，免得用例之间互相改到同一份对象。 */
function previewBody() {
  return {
    drafts: [
      {
        name: '用 gdb 定位一次段错误',
        intro: '用 gdb 找出崩在哪一行。',
        description: '给定一段会崩的程序。\n\n![第 2 页-图 1](https://storage.test/task-images/a.png)',
        space: SPACE_ID,
        categoryId: 3,
      },
      {
        name: '手写一个最简内存分配器',
        intro: '实现 malloc / free 的最简版本。',
        description: '实现 malloc / free 的最简版本，说明碎片是怎么来的。',
        space: SPACE_ID,
        categoryId: 3,
      },
    ],
    templateUsed: { title: '计算机系统基础 · 标准题模板' },
    tokenUsed: 18742,
    // 预览这一步服务端顺带落好的附件行：原 PDF 一份、抽出的插图一张。id 是后端
    // 数据库里的号，前端只负责在勾中的时候原样报回去。
    attachments: {
      pdf: {
        id: 911,
        name: '计算机系统基础-第五次作业.pdf',
        size: 1024,
        contentType: 'application/pdf',
      },
      images: [
        {
          id: 912,
          name: 'input.pdf-0001-01.png',
          size: 2048,
          contentType: 'image/png',
        },
      ],
    },
  }
}

/** 走完整条前半程：切到 PDF、选文件、解析。 */
async function parsed(view: Awaited<ReturnType<typeof mount>>) {
  await switchToPdf(view)
  await pick(view.getByLabelText('上传题目 PDF') as HTMLInputElement, pdfFile())
  await fireEvent.click(view.getByRole('button', { name: '解析成题目草稿' }))
  await waitFor(() => expect(view.container.querySelector('[data-testid="pdf-meta"]')).not.toBeNull())
  return view
}

describe('发题页：手写一道 / 从 PDF 生成', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    spaceDetail.mockImplementation(async () => ({ data: { space: { id: SPACE_ID, name: '数据结构空间' } } }))
    listCategories.mockImplementation(async () => ({ data: { categories: [{ id: 3, name: '基础题' }] } }))
    previewFromPdf.mockImplementation(async () => ({ data: previewBody() }))
    confirmFromPdf.mockImplementation(async () => ({
      data: { tasks: [{ id: 101, name: '用 gdb 定位一次段错误' }], count: 1 },
    }))
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('头上有两颗态，默认手写一道，切过去就不再是老页', async () => {
    const view = await mount()

    // 两颗态都在，默认那一颗是手写一道 —— 所以底下还是老发题页。
    expect(view.getByRole('button', { name: '手写一道' })).toBeTruthy()
    expect(view.getByRole('button', { name: '从 PDF 生成' })).toBeTruthy()
    expect(view.container.querySelector('[data-testid="write-probe"]')).not.toBeNull()
    expect(view.queryByLabelText('上传题目 PDF')).toBeNull()

    await switchToPdf(view)

    // 换态之后：上传框在，老发题页不在了。
    expect(view.getByLabelText('上传题目 PDF')).toBeTruthy()
    expect(view.container.querySelector('[data-testid="write-probe"]')).toBeNull()
    // 三条上限写在页面上，不是只写在代码里。
    const upload = view.getByText('真平台只收 PDF，单个文件最大 15MB，一次最多解析 20 道题 —— 这三条是后端写死的上限')
    expect(upload).toBeTruthy()
  })

  it('解析：浏览器真的把这份 PDF 发出去了，结果区摆的是接口回来的那三件事', async () => {
    const view = await parsed(await mount())

    // 发出去的那条请求 —— 文件、空间、上限，一样不少。
    expect(previewFromPdf).toHaveBeenCalledTimes(1)
    const sent = previewFromPdf.mock.calls[0][0] as { spaceId: number; file: File; maxTasks: number }
    expect(sent.spaceId).toBe(SPACE_ID)
    expect(sent.file.name).toBe('计算机系统基础-第五次作业.pdf')
    expect(sent.maxTasks).toBe(20)

    // 结果区：模板、插图数（正文里那张图，1 张）、token —— 都是接口回来的那几个值。
    expect(textOf(view.container, 'pdf-template')).toBe('模板：计算机系统基础 · 标准题模板')
    expect(textOf(view.container, 'pdf-images')).toBe('抽出插图 1 张')
    expect(textOf(view.container, 'pdf-tokens')).toBe('消耗 18,742 tokens')
    // 并且说清楚：草稿还不是题目。
    expect(textOf(view.container, 'pdf-meta') ?? '').toContain('还没有成为题目')

    // 两条草稿都在屏幕上，标题与题干就是接口给的那份。
    const titles = view.getAllByLabelText('标题') as HTMLInputElement[]
    expect(titles.map((i) => i.value)).toEqual(['用 gdb 定位一次段错误', '手写一个最简内存分配器'])
    const bodies = view.getAllByLabelText('题干') as HTMLTextAreaElement[]
    expect(bodies[0].value).toContain('给定一段会崩的程序')
    // 出处页摆在每一条上。
    expect(view.getAllByTestId('draft-origin').map((e) => e.textContent?.trim())).toEqual([
      'PDF · 第 1 页',
      'PDF · 第 2 页',
    ])
  })

  it('勾掉一条、改两处：确认发走的就是屏幕上那几条，而且不跳走', async () => {
    const view = await parsed(await mount())

    // 两条都勾着 —— 按钮跟着数字走。
    expect(view.getByRole('button', { name: '确认发布 2 道' })).toBeTruthy()
    await toggle(view.getByLabelText('勾选「手写一个最简内存分配器」'))
    await waitFor(() => expect(view.getByRole('button', { name: '确认发布 1 道' })).toBeTruthy())

    // 就地改标题与题干。
    await fireEvent.update(view.getAllByLabelText('标题')[0], '用 gdb 定位一次段错误（改过）')
    await fireEvent.update(view.getAllByLabelText('题干')[0], '改过的题干：找出崩在哪一行，并把寄存器状态截图交上来。')

    const before = view.router.currentRoute.value.fullPath
    await fireEvent.click(view.getByRole('button', { name: '确认发布 1 道' }))
    await waitFor(() => expect(confirmFromPdf).toHaveBeenCalledTimes(1))

    // 发出去的草稿：只有勾中的那一条，文字是改过的那份，出处标记加上去了。
    const sent = confirmFromPdf.mock.calls[0][0] as {
      drafts: { name: string; intro: string; description: string; space: number; categoryId?: number }[]
      taskOptions: { space: number; attachmentIds?: number[] }
    }
    expect(sent.drafts).toHaveLength(1)
    expect(sent.drafts[0].name).toBe('用 gdb 定位一次段错误（改过）')
    expect(sent.drafts[0].description).toBe('改过的题干：找出崩在哪一行，并把寄存器状态截图交上来。')
    expect(sent.drafts[0].space).toBe(SPACE_ID)
    expect(sent.drafts[0].categoryId).toBe(3)
    // 出处标记在简介里（题目模型没有来源这一列）—— 队列那一行显示的就是简介。
    expect(sent.drafts[0].intro).toBe('【PDF · 第 1 页】用 gdb 找出崩在哪一行。')
    // 两颗勾默认都勾着，所以这条请求里带着两份文件的行号：原 PDF 在前、插图在后。
    expect(sent.taskOptions.attachmentIds).toEqual([911, 912])

    // 不跳走：地址栏还是发题这一页。
    expect(view.router.currentRoute.value.fullPath).toBe(before)

    // 就地给回执，回执里两个去处都指向新外壳那棵树。
    expect(textOf(view.container, 'pdf-receipt') ?? '').toContain('刚发的 1 道题已经进了待审核队列')
    const hrefs = Array.from(view.container.querySelectorAll('a')).map((a) => a.getAttribute('href'))
    expect(hrefs).toContain(`/spaces/${SPACE_ID}/board/review`)
    expect(hrefs).toContain(`/spaces/${SPACE_ID}/board/mine`)
  })

  it('附件：两颗勾默认都勾着，标签写的是哪一份、几张，取消勾的那一份就不跟着走', async () => {
    const view = await parsed(await mount())

    // 拉起来就是原型那两颗勾，默认都勾着。
    const pdfBox = view.getByLabelText('原 PDF') as HTMLInputElement
    const imgBox = view.getByLabelText('抽出的插图（1 张）') as HTMLInputElement
    expect(pdfBox.checked).toBe(true)
    expect(imgBox.checked).toBe(true)
    // 勾的是什么摆出来给人看：文件名字是接口回来的那一份。
    expect(textOf(view.container, 'pdf-attach-pdf-file')).toBe('原 PDF：计算机系统基础-第五次作业.pdf')
    expect(textOf(view.container, 'pdf-attach-image-files')).toBe('插图：input.pdf-0001-01.png')
    expect(textOf(view.container, 'pdf-attach-count')).toBe('这 2 个文件会附在每一道生成出来的题上')

    // 取消勾插图：跟着走的只剩原 PDF 那一份，数字也跟着掉。
    await toggle(imgBox)
    await waitFor(() =>
      expect(textOf(view.container, 'pdf-attach-count')).toBe('这 1 个文件会附在每一道生成出来的题上')
    )

    await fireEvent.click(view.getByRole('button', { name: '确认发布 2 道' }))
    await waitFor(() => expect(confirmFromPdf).toHaveBeenCalledTimes(1))
    const sent = confirmFromPdf.mock.calls[0][0] as { taskOptions: { attachmentIds?: number[] } }
    // 发出去的就是屏幕上勾着的那一份，行号原样 —— 原 PDF 在前。
    expect(sent.taskOptions.attachmentIds).toEqual([911])
  })

  it('附件：一样都不勾的时候，请求里没有 attachmentIds 这一项（与从前一致）', async () => {
    const view = await parsed(await mount())

    await toggle(view.getByLabelText('原 PDF') as HTMLInputElement)
    await toggle(view.getByLabelText('抽出的插图（1 张）') as HTMLInputElement)
    await waitFor(() =>
      expect(textOf(view.container, 'pdf-attach-count')).toBe('这 0 个文件会附在每一道生成出来的题上')
    )

    await fireEvent.click(view.getByRole('button', { name: '确认发布 2 道' }))
    await waitFor(() => expect(confirmFromPdf).toHaveBeenCalledTimes(1))
    const sent = confirmFromPdf.mock.calls[0][0] as { taskOptions: Record<string, unknown> }
    expect('attachmentIds' in sent.taskOptions).toBe(false)
  })

  it('附件：接口没落成文件行的那一样不画勾，页面上说清为什么', async () => {
    previewFromPdf.mockImplementation(async () => ({
      data: { ...previewBody(), attachments: { pdf: null, images: [] } },
    }))
    const view = await parsed(await mount())

    // 一颗勾都不画 —— 点了也带不走的东西，不画成勾。
    expect(view.container.querySelector('[data-testid="pdf-attach-pdf"]')).toBeNull()
    expect(view.container.querySelector('[data-testid="pdf-attach-images"]')).toBeNull()
    expect(view.queryByLabelText('原 PDF')).toBeNull()
    // 但要说清是哪一样、为什么。
    expect(textOf(view.container, 'pdf-attach-pdf-why') ?? '').toContain('没画')
    expect(textOf(view.container, 'pdf-attach-images-why') ?? '').toContain('没画')

    // 确认发布照样走得通，请求形状与从前一样。
    await fireEvent.click(view.getByRole('button', { name: '确认发布 2 道' }))
    await waitFor(() => expect(confirmFromPdf).toHaveBeenCalledTimes(1))
    const sent = confirmFromPdf.mock.calls[0][0] as { taskOptions: Record<string, unknown> }
    expect('attachmentIds' in sent.taskOptions).toBe(false)
  })

  it('超限与错类型：一个请求都不发出去，屏幕上说清为什么', async () => {
    const view = await mount()
    await switchToPdf(view)

    const oversized = pdfFile('太大了.pdf', 15 * 1024 * 1024 + 1)
    await pick(view.getByLabelText('上传题目 PDF') as HTMLInputElement, oversized)
    await fireEvent.click(view.getByRole('button', { name: '解析成题目草稿' }))
    await waitFor(() => expect(textOf(view.container, 'pdf-error') ?? '').toContain('不能超过 15MB'))
    expect(previewFromPdf).not.toHaveBeenCalled()

    // 换一份不是 PDF 的：同样是本地就拦下来。
    const wrong = new File([new Uint8Array(10)], '题目.docx', {
      type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    })
    await pick(view.getByLabelText('上传题目 PDF') as HTMLInputElement, wrong)
    await fireEvent.click(view.getByRole('button', { name: '解析成题目草稿' }))
    await waitFor(() => expect(textOf(view.container, 'pdf-error') ?? '').toContain('只收 PDF'))
    expect(previewFromPdf).not.toHaveBeenCalled()
  })

  it('后端拒绝的时候，屏幕上就是它那句话，而且不会凭空画出草稿', async () => {
    previewFromPdf.mockImplementation(async () => {
      throw { response: { data: { message: 'LLM is not configured' } }, message: 'Request failed with status code 400' }
    })

    const view = await mount()
    await switchToPdf(view)
    await pick(view.getByLabelText('上传题目 PDF') as HTMLInputElement, pdfFile())
    await fireEvent.click(view.getByRole('button', { name: '解析成题目草稿' }))

    await waitFor(() => expect(textOf(view.container, 'pdf-error')).toBe('解析失败：LLM is not configured'))
    // 没有解析结果、没有草稿、没有确认按钮 —— 一个都没画。
    expect(view.container.querySelector('[data-testid="pdf-meta"]')).toBeNull()
    expect(view.container.querySelector('[data-testid="pdf-drafts"]')).toBeNull()
    expect(view.queryByRole('button', { name: /确认发布/ })).toBeNull()
  })
})
