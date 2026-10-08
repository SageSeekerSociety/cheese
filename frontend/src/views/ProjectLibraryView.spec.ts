/**
 * 资料库这一页：给进这个项目的文件都有什么、谁在哪给的、里面是什么，以及怎么放进
 * 来、换新、拿走、扔掉。
 *
 * 规矩：扔掉和替换都先问一句，答应了才动；替换是同一个名字换新的字节，不是多一份；
 * 看哪一份记在地址上。
 */
import type { LibraryFile } from '../api'

import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { setLocale, t } from '../i18n'

import ProjectLibraryView from './ProjectLibraryView.vue'

vi.mock('../api', () => ({
  listProjectLibrary: vi.fn(),
  deleteLibraryFile: vi.fn(),
  downloadFile: vi.fn(),
  libraryFileRawUrl: (projectId: string, path: string) => `/api/projects/${projectId}/library/raw?path=${path}`,
}))

// 文档那一半在 ProjectLibraryView.documents.spec.ts；这里资料库里没有文档。
vi.mock('../api/projectDocuments', () => ({
  listProjectDocuments: vi.fn(async () => ({ data: [] })),
  searchProjectDocuments: vi.fn(async (_: string, query: string) => ({ query, library: [], rooms: [] })),
  createProjectDocument: vi.fn(),
  deleteDocument: vi.fn(),
  getDocumentAbout: vi.fn(),
}))

vi.mock('../lib/libraryApi', () => ({
  uploadLibraryFile: vi.fn(),
  replaceLibraryFile: vi.fn(),
  moveLibraryFile: vi.fn(),
  libraryFileBytes: vi.fn(),
}))

const { deleteLibraryFile, downloadFile, listProjectLibrary } = await import('../api')
const { libraryFileBytes, moveLibraryFile, replaceLibraryFile, uploadLibraryFile } = await import('../lib/libraryApi')

afterEach(cleanup)

// 确认框是一个 VOverlay，而 happy-dom 没有 visualViewport：不补这几样，弹窗根本
// 挂不上去，测到的就成了「按下删除什么也没发生」。
beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  // 桌面上一行的 ⋯ 是一个 v-menu，定位时要读 devicePixelRatio。
  if (!('devicePixelRatio' in globalThis)) {
    Object.defineProperty(globalThis, 'devicePixelRatio', { configurable: true, value: 1 })
  }
  if (!globalThis.visualViewport) {
    Object.defineProperty(globalThis, 'visualViewport', {
      configurable: true,
      value: { width: 1024, height: 768, offsetLeft: 0, offsetTop: 0, addEventListener() {}, removeEventListener() {} },
    })
  }
})

function file(path: string, extra: Partial<LibraryFile> = {}): LibraryFile {
  return {
    path,
    bytes: 2048,
    modified: 1758000000,
    added_by: 'alice',
    added_at: '2026-09-20T10:00:00Z',
    room: null,
    replaced: 0,
    references: 0,
    ...extra,
  }
}

beforeEach(() => {
  setLocale('zh-CN')
  vi.clearAllMocks()
  vi.mocked(listProjectLibrary).mockResolvedValue({
    data: [
      file('预算表(2).xlsx', { room: { id: 't1', title: '数据分析' } }),
      file('预算表.xlsx', { bytes: 120 }),
      file('结题报告.docx'),
    ],
    total: 3,
  })
  vi.mocked(deleteLibraryFile).mockResolvedValue({ deleted: true })
  vi.mocked(downloadFile).mockResolvedValue(undefined)
  vi.mocked(uploadLibraryFile).mockResolvedValue({ path: '新.txt', bytes: 1 })
  vi.mocked(replaceLibraryFile).mockResolvedValue({ path: '预算表.xlsx', bytes: 1 })
  vi.mocked(libraryFileBytes).mockResolvedValue(new ArrayBuffer(0))
})

const Blank = defineComponent({ render: () => h('div') })

async function renderRaw(url = '/projects/p1/library') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/projects/:projectId/library', name: 'project-library', component: ProjectLibraryView, props: true },
      { path: '/projects/:projectId/topics/:topicId', name: 'workspace-topic', component: Blank },
    ],
  })
  await router.push(url)
  await router.isReady()
  const Host = defineComponent({ setup: () => () => h(components.VApp, null, () => h(RouterView)) })
  const view = render(Host, {
    global: { plugins: [createVuetify({ components, directives }), router, createPinia()] },
  })
  return { ...view, router }
}

async function mount(url = '/projects/p1/library') {
  const view = await renderRaw(url)
  await waitFor(() => expect(view.container.textContent).toContain('预算表.xlsx'))
  return view
}

const rowNames = (container: Element) =>
  Array.from(container.querySelectorAll('.library-row__name')).map((el) => el.textContent)

function pick(input: HTMLInputElement, files: File[]) {
  Object.defineProperty(input, 'files', { configurable: true, value: files })
  return fireEvent.change(input)
}

describe('资料库', () => {
  it('把给进来的文件列出来，同名的那两份各占一行，并写明是谁给的', async () => {
    const { container } = await mount()
    expect(rowNames(container)).toEqual(['预算表(2).xlsx', '预算表.xlsx', '结题报告.docx'])
    expect(container.textContent).toContain('alice')
    expect(container.textContent).toContain('2.0 KB')
  })

  it('按名字搜、按类型筛', async () => {
    const { container } = await mount()
    await fireEvent.update(screen.getByRole('searchbox'), '报告')
    await waitFor(() => expect(rowNames(container)).toEqual(['结题报告.docx']))

    await fireEvent.update(screen.getByRole('searchbox'), '')
    await fireEvent.click(screen.getByRole('button', { name: '表格' }))
    await waitFor(() => expect(rowNames(container)).toEqual(['预算表(2).xlsx', '预算表.xlsx']))
  })

  it('点开一份：地址记着是哪一份，读的是资料库里那一份的内容，写明是在哪个对话里给的', async () => {
    const { container, router } = await mount()
    await fireEvent.click(screen.getByRole('button', { name: /^预算表\(2\)\.xlsx alice/ }))
    await waitFor(() => expect(router.currentRoute.value.query.file).toBe('预算表(2).xlsx'))
    await waitFor(() => expect(libraryFileBytes).toHaveBeenCalledWith('p1', '预算表(2).xlsx', false))
    const link = screen.getByRole('link', { name: '《数据分析》' })
    expect(link.getAttribute('href')).toBe('/projects/p1/topics/t1')
    expect(container.textContent).toContain('数据分析')
  })

  it('Word 文档要服务端转好的那一份才画得出来', async () => {
    await mount('/projects/p1/library?file=结题报告.docx')
    await waitFor(() => expect(libraryFileBytes).toHaveBeenCalledWith('p1', '结题报告.docx', true))
  })

  it('下载取的是资料库里那一份，不经过某个房间', async () => {
    await mount('/projects/p1/library?file=预算表.xlsx')
    await fireEvent.click(screen.getByRole('button', { name: '下载' }))
    expect(downloadFile).toHaveBeenCalledWith('/api/projects/p1/library/raw?path=预算表.xlsx', '预算表.xlsx')
  })

  it('在这一页上放进来的每一份都上传，放完重新列一遍', async () => {
    const { container } = await mount()
    const input = container.querySelector('input[type="file"][multiple]') as HTMLInputElement
    await pick(input, [new File(['a'], 'a.txt'), new File(['b'], 'b.txt')])
    await waitFor(() => expect(uploadLibraryFile).toHaveBeenCalledTimes(2))
    expect(vi.mocked(uploadLibraryFile).mock.calls.map((call) => (call[1] as File).name)).toEqual(['a.txt', 'b.txt'])
    await waitFor(() => expect(listProjectLibrary).toHaveBeenCalledTimes(2))
  })

  it('替换先问一句，答应了才换；取消什么也不动', async () => {
    const { container, baseElement } = await mount('/projects/p1/library?file=预算表.xlsx')
    const input = container.querySelector('input[type="file"]:not([multiple])') as HTMLInputElement

    await fireEvent.click(screen.getByRole('button', { name: '替换为新版本' }))
    await pick(input, [new File(['new'], '预算表-新.xlsx')])
    await waitFor(() => expect(baseElement.textContent).toContain('引用这份文件的消息将读到新的一份'))
    const cancel = Array.from(baseElement.querySelectorAll('.v-card-actions button')).find(
      (b) => b.textContent?.trim() === '取消'
    )
    await fireEvent.click(cancel!)
    expect(replaceLibraryFile).not.toHaveBeenCalled()

    await fireEvent.click(screen.getByRole('button', { name: '替换为新版本' }))
    await pick(input, [new File(['new'], '预算表-新.xlsx')])
    const confirm = await waitFor(() => {
      const button = Array.from(baseElement.querySelectorAll('.v-card-actions button')).find(
        (b) => b.textContent?.trim() === '替换'
      )
      expect(button).toBeTruthy()
      return button as HTMLElement
    })
    await fireEvent.click(confirm)
    await waitFor(() => expect(replaceLibraryFile).toHaveBeenCalledWith('p1', '预算表.xlsx', expect.any(File)))
  })

  it('删除先问一句，答应了才真的删', async () => {
    const { container, baseElement } = await mount()
    await fireEvent.click(screen.getByRole('button', { name: '预算表(2).xlsx 的操作' }))
    const remove = await waitFor(() => {
      const item = Array.from(baseElement.querySelectorAll('.v-list-item, [role="menuitem"]')).find(
        (b) => b.textContent?.trim() === '删除'
      )
      expect(item).toBeTruthy()
      return item as HTMLElement
    })
    await fireEvent.click(remove)
    await waitFor(() => expect(baseElement.textContent).toContain('删除后无法恢复'))
    expect(deleteLibraryFile).not.toHaveBeenCalled()

    const confirm = Array.from(baseElement.querySelectorAll('.v-card-actions button')).find(
      (b) => b.textContent?.trim() === '删除'
    )
    await fireEvent.click(confirm!)
    await waitFor(() => expect(deleteLibraryFile).toHaveBeenCalledWith('p1', '预算表(2).xlsx'))
    await waitFor(() => expect(rowNames(container)).not.toContain('预算表(2).xlsx'))
  })

  it('一份都还没有时说的是暂无资料', async () => {
    vi.mocked(listProjectLibrary).mockResolvedValue({ data: [], total: 0 })
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/projects/:projectId/library', component: ProjectLibraryView, props: true }],
    })
    await router.push('/projects/p1/library')
    await router.isReady()
    const Host = defineComponent({ setup: () => () => h(components.VApp, null, () => h(RouterView)) })
    const { container } = render(Host, {
      global: { plugins: [createVuetify({ components, directives }), router, createPinia()] },
    })
    await waitFor(() => expect(container.textContent).toContain('暂无资料'))
  })
})

/** 名单整块没读出来：就地换成错误 + 重试（docs/design-system.md §3.10），不能退化
 *  成一个红色的「暂无资料」——那是「本来就没有」，不是「没读到」。
 */
describe('资料库读不到时', () => {
  it('就地显示原因和重试，而不是装作「暂无资料」', async () => {
    vi.mocked(listProjectLibrary).mockRejectedValueOnce(new Error('服务器错误'))
    const { container } = await renderRaw()

    await waitFor(() => expect(container.textContent).toContain('无法读取资料库'))
    expect(container.textContent).toContain('服务器错误')
    expect(container.textContent).not.toContain('暂无资料')
    expect(screen.getByRole('button', { name: t('global.loadError.retry') })).toBeTruthy()
  })

  it('点重试真的再问一遍服务端', async () => {
    vi.mocked(listProjectLibrary).mockRejectedValueOnce(new Error('服务器错误'))
    const { container } = await renderRaw()
    await waitFor(() => expect(container.textContent).toContain('无法读取资料库'))
    expect(listProjectLibrary).toHaveBeenCalledTimes(1)

    vi.mocked(listProjectLibrary).mockResolvedValueOnce({ data: [file('结题报告.docx')], total: 1 })
    await fireEvent.click(screen.getByRole('button', { name: t('global.loadError.retry') }))

    await waitFor(() => expect(listProjectLibrary).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(rowNames(container)).toEqual(['结题报告.docx']))
  })

  it('有资料但被筛掉了：说的是「筛掉了」并给一键清除，和「暂无资料」分开', async () => {
    const { container } = await mount()
    await fireEvent.update(screen.getByRole('searchbox'), '对不上的名字')
    await waitFor(() => expect(container.textContent).toContain('暂无匹配的资料'))
    expect(container.textContent).not.toContain('暂无资料')

    await fireEvent.click(screen.getByRole('button', { name: '清除筛选' }))
    await waitFor(() => expect(rowNames(container)).toEqual(['预算表(2).xlsx', '预算表.xlsx', '结题报告.docx']))
  })
})

/** 名字里的 `/` 是文件夹：不搜不筛时一层一层地看，一搜就是整个资料库。 */
describe('资料库的文件夹', () => {
  beforeEach(() => {
    vi.mocked(listProjectLibrary).mockResolvedValue({
      data: [file('合同/2026/报价.xlsx'), file('合同/附件.pdf'), file('结题报告.docx')],
      total: 3,
    })
    vi.mocked(moveLibraryFile).mockResolvedValue({ moved: {} })
  })

  async function open(url = '/projects/p1/library') {
    const view = await renderRaw(url)
    await waitFor(() => expect(rowNames(view.container).length).toBeGreaterThan(0))
    return view
  }

  async function menuItem(baseElement: Element, owner: string, label: string) {
    await fireEvent.click(screen.getByRole('button', { name: `${owner} 的操作` }))
    return waitFor(() => {
      const item = Array.from(baseElement.querySelectorAll('.v-list-item, [role="menuitem"]')).find(
        (b) => b.textContent?.trim() === label
      )
      expect(item).toBeTruthy()
      return item as HTMLElement
    })
  }

  function dialogButton(baseElement: Element, label: string) {
    return Array.from(baseElement.querySelectorAll('button')).find(
      (b) => b.textContent?.trim() === label
    ) as HTMLElement
  }

  it('最上层先列文件夹，点进去是那一层，点所在位置回到上一层', async () => {
    const { container, router } = await open()
    expect(rowNames(container)).toEqual(['合同', '结题报告.docx'])

    await fireEvent.click(screen.getByText('合同'))
    await waitFor(() => expect(rowNames(container)).toEqual(['2026', '附件.pdf']))
    expect(router.currentRoute.value.query.dir).toBe('合同')

    await fireEvent.click(screen.getByRole('button', { name: t('navigation.project.library') }))
    await waitFor(() => expect(rowNames(container)).toEqual(['合同', '结题报告.docx']))
  })

  it('一搜就是整个资料库里对得上的，写完整路径', async () => {
    const { container } = await open()
    await fireEvent.update(screen.getByRole('searchbox', { name: '搜索标题和内容' }), '报价')
    await waitFor(() => expect(rowNames(container)).toEqual(['合同/2026/报价.xlsx']))
  })

  it('在一个文件夹里上传，就放进这个文件夹', async () => {
    const { container } = await open('/projects/p1/library?dir=合同')
    const input = container.querySelector('input[type="file"][multiple]') as HTMLInputElement
    await pick(input, [new File(['a'], 'a.txt')])
    await waitFor(() => expect(uploadLibraryFile).toHaveBeenCalledWith('p1', expect.any(File), '合同'))
  })

  it('移动或重命名把新的完整名字交给服务端，再重新列一遍', async () => {
    const { baseElement } = await open('/projects/p1/library?dir=合同')
    await fireEvent.click(await menuItem(baseElement, '附件.pdf', '移动或重命名'))
    const name = await waitFor(() => screen.getByLabelText('名称') as HTMLInputElement)
    expect(name.value).toBe('附件.pdf')
    await fireEvent.update(name, '附件-旧.pdf')
    await fireEvent.click(dialogButton(baseElement, '移动'))
    await waitFor(() => expect(moveLibraryFile).toHaveBeenCalledWith('p1', '合同/附件.pdf', '合同/附件-旧.pdf'))
    await waitFor(() => expect(listProjectLibrary).toHaveBeenCalledTimes(2))
  })

  it('删除一个文件夹先说清里面几份会一起删，答应了才删', async () => {
    const { container, baseElement } = await open()
    await fireEvent.click(await menuItem(baseElement, '合同', '删除'))
    await waitFor(() => expect(baseElement.textContent).toContain('文件夹里的 2 份文件会一起删除'))
    expect(deleteLibraryFile).not.toHaveBeenCalled()

    const confirm = Array.from(baseElement.querySelectorAll('.v-card-actions button')).find(
      (b) => b.textContent?.trim() === '删除'
    )
    await fireEvent.click(confirm!)
    await waitFor(() => expect(deleteLibraryFile).toHaveBeenCalledWith('p1', '合同'))
    await waitFor(() => expect(rowNames(container)).toEqual(['结题报告.docx']))
  })
})
