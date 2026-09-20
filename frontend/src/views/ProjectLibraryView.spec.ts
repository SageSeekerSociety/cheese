/**
 * 资料库这一页：给进这个项目的文件，在哪儿看得见、怎么拿走、怎么扔掉。
 *
 * 上传不在这一页上——一份资料总是在说某件事的时候给进来的，入口只有输入栏那一个。
 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import ProjectLibraryView from './ProjectLibraryView.vue'

vi.mock('../api', () => ({
  listProjectLibrary: vi.fn(),
  deleteLibraryFile: vi.fn(),
  downloadFile: vi.fn(),
  libraryFileRawUrl: (projectId: string, path: string) => `/api/projects/${projectId}/library/raw?path=${path}`,
}))

const { deleteLibraryFile, downloadFile, listProjectLibrary } = await import('../api')

const vuetify = createVuetify({ components, directives })

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
  if (!globalThis.visualViewport) {
    Object.defineProperty(globalThis, 'visualViewport', {
      configurable: true,
      value: { width: 1024, height: 768, offsetLeft: 0, offsetTop: 0, addEventListener() {}, removeEventListener() {} },
    })
  }
})

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(listProjectLibrary).mockResolvedValue({
    data: [
      { path: '预算表(2).xlsx', bytes: 2048, modified: 1758000000 },
      { path: '预算表.xlsx', bytes: 120, modified: 1757000000 },
    ],
    total: 2,
  })
  vi.mocked(deleteLibraryFile).mockResolvedValue({ deleted: true })
  vi.mocked(downloadFile).mockResolvedValue(undefined)
})

function mount() {
  return render(ProjectLibraryView, { props: { projectId: 'p1' }, global: { plugins: [vuetify] } })
}

function button(container: Element, label: string): HTMLElement | undefined {
  return Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.trim() === label)
}

describe('资料库', () => {
  it('把给进来的文件列出来，同名的那两份各占一行', async () => {
    const { container } = mount()

    await waitFor(() => expect(container.textContent).toContain('预算表.xlsx'))
    expect(container.textContent).toContain('预算表(2).xlsx')
    expect(container.textContent).toContain('2.0 KB')
  })

  it('下载取的是资料库里那一份，不经过某个房间', async () => {
    const { container } = mount()
    await waitFor(() => expect(container.textContent).toContain('预算表.xlsx'))

    await fireEvent.click(button(container, '下载')!)

    expect(downloadFile).toHaveBeenCalledWith('/api/projects/p1/library/raw?path=预算表(2).xlsx', '预算表(2).xlsx')
  })

  it('删除先问一句，答应了才真的删', async () => {
    const { container, baseElement } = mount()
    await waitFor(() => expect(container.textContent).toContain('预算表.xlsx'))

    await fireEvent.click(button(container, '删除')!)
    await waitFor(() => expect(baseElement.textContent).toContain('删除后无法恢复'))
    expect(deleteLibraryFile).not.toHaveBeenCalled()

    const confirm = Array.from(baseElement.querySelectorAll('.v-card-actions button')).find(
      (b) => b.textContent?.trim() === '删除'
    )
    await fireEvent.click(confirm!)

    await waitFor(() => expect(deleteLibraryFile).toHaveBeenCalledWith('p1', '预算表(2).xlsx'))
    await waitFor(() => expect(container.textContent).not.toContain('预算表(2).xlsx'))
  })

  it('一份都还没有时说的是暂无资料，并且说清文件从哪儿来', async () => {
    vi.mocked(listProjectLibrary).mockResolvedValue({ data: [], total: 0 })
    const { container } = mount()

    await waitFor(() => expect(container.textContent).toContain('暂无资料'))
    expect(container.textContent).toContain('在对话里上传的文件会收进这里')
  })
})
