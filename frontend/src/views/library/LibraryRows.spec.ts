// 资料库那一列的行：一样东西写什么名字、给谁看的是哪一份、每一行能做的事怎么报上去。
//
// 这一层只管摆和上报：点开、下载、替换、改名、版本、删除都报给页面，谁都不在这儿发生。
// 删除在这里只是报上去——「先问一句」是页面的事，本文件不该出现任何请求或对话框。
import type { ProjectDocument } from '../../api/projectDocuments'
import type { LibraryRow } from '../../composables/useLibraryPages'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { setLocale } from '../../i18n'
import { libraryFile, listedDocument } from '../../test/fakeLibrary'

import LibraryRows from './LibraryRows.vue'

afterEach(cleanup)

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
    width: 1280,
    height: 800,
    offsetLeft: 0,
    offsetTop: 0,
    scale: 1,
    addEventListener() {},
    removeEventListener() {},
  })
})

beforeEach(() => setLocale('zh-CN'))

function doc(id: string, title: string, author: string): ProjectDocument {
  return {
    id,
    project_id: 'p1',
    kind: 'doc',
    title,
    doc_version: 1,
    author,
    created_at: '2026-09-21T10:00:00Z',
    updated_at: '2026-09-21T10:00:00Z',
  }
}

const folderRow: LibraryRow = {
  key: 'dir:合同',
  rank: '1',
  folder: { type: 'folder', rank: '1', path: '合同', name: '合同', count: 2, modified: 1758000300 },
}
const fileRow: LibraryRow = { key: 'file:合同/报价.xlsx', rank: '2', file: libraryFile('合同/报价.xlsx') }

function mount(rows: LibraryRow[], browsing = true) {
  return render(LibraryRows, {
    props: {
      rows,
      scroller: null,
      selectedPath: '',
      busy: '',
      browsing,
      members: [{ user_handle: 'alice', name: 'Alice', name_source: 'human' }],
      agentName: '芝士',
    },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

const rowNames = (container: Element) =>
  Array.from(container.querySelectorAll('.library-row__name')).map((el) => el.textContent)

/** 行上写着名字的那颗按钮：点它就是「打开这一份」。 */
const openButton = (name: string) => screen.getByText(name).closest('button') as HTMLElement

describe('资料库那一列的行', () => {
  it('一层一层看时写最后一层的名字，搜出来时写完整路径', () => {
    const browsing = mount([folderRow, fileRow])
    expect(rowNames(browsing.container)).toEqual(['合同', '报价.xlsx'])
    cleanup()

    const searching = mount([folderRow, fileRow], false)
    expect(rowNames(searching.container)).toEqual(['合同', '合同/报价.xlsx'])
  })

  it('点一份文件报的是那一份，点一个文件夹报的是那一层', async () => {
    const view = mount([folderRow, fileRow])

    // 事件名照组件声明里的写法（模板那边 `@open-folder` 映到它）。
    await fireEvent.click(openButton('合同'))
    expect(view.emitted()['openFolder']).toEqual([['合同']])

    await fireEvent.click(openButton('报价.xlsx'))
    expect(view.emitted()['openFile']).toEqual([[fileRow.file]])
  })

  it('一份文档行里写的是改它的人的名字，不是 handle', () => {
    const { container } = mount([
      { key: 'doc:d1', rank: '3', doc: listedDocument(doc('d1', '竞品定价对比', 'cheese')) },
    ])
    expect(container.textContent).toContain('竞品定价对比')
    expect(container.textContent).toContain('芝士')
  })

  it('行上的「删除」只是把这一份报上去，自己不问也不动', async () => {
    const view = mount([fileRow])
    await fireEvent.click(screen.getByRole('button', { name: /^报价\.xlsx 的操作/ }))
    const item = await waitFor(() => {
      const found = Array.from(document.querySelectorAll('.v-list-item, [role="menuitem"]')).find(
        (b) => b.textContent?.trim() === '删除'
      )
      expect(found).toBeTruthy()
      return found as HTMLElement
    })
    await fireEvent.click(item)

    expect(view.emitted()['delete']).toEqual([[fileRow.file]])
    // 这一行还在：删不删由页面拿着确认框说了算。
    expect(rowNames(view.container)).toEqual(['报价.xlsx'])
  })
})
