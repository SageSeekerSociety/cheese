// 资料库那一列：文件多了（过 VIRTUAL_LIST_CONTENT_THRESHOLD）就交给 VirtualList，以下
// 整列照画 —— 整列都在 DOM 里时 Ctrl+F 和读屏摸得到每一份，行里的按钮和 ⋯ 菜单也照旧。
//
// happy-dom 不排版，量不出「屏幕上挂着几行」。这里问的是**代码走到了哪条路**：过没过
// 门槛、给 virtua 的数据对不对、外壳还是不是一个 `li`。`virtua/vue` 换成记账替身（同
// common/VirtualList.spec.ts）。
import type { LibraryFile } from '../api'

import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { setLocale } from '../i18n'
import { VIRTUAL_LIST_CONTENT_THRESHOLD } from '../lib/virtualList'

const virtua = vi.hoisted(() => ({
  /** 每次交给 virtua 的那份 props（读它反映到的那份最新数据）。 */
  seen: [] as Record<string, unknown>[],
}))

vi.mock('virtua/vue', async () => {
  const { defineComponent: define, h: hyperscript } = await import('vue')
  return {
    Virtualizer: define({
      name: 'Virtualizer',
      props: ['data', 'itemSize', 'bufferSize', 'keepMounted', 'itemProps', 'scrollRef', 'item', 'as', 'role'],
      setup(props, { attrs, slots }) {
        virtua.seen.push(props as unknown as Record<string, unknown>)
        return () =>
          hyperscript(
            'div',
            { 'data-virtua': 'Virtualizer', ...attrs },
            ((props.data as unknown[]) ?? []).map((item, index) =>
              hyperscript((props.item as string) || 'div', slots.default?.({ item, index }))
            )
          )
      },
    }),
  }
})

vi.mock('../api', () => ({
  listProjectLibrary: vi.fn(),
  deleteLibraryFile: vi.fn(),
  downloadFile: vi.fn(),
  libraryFileRawUrl: (projectId: string, path: string) => `/api/projects/${projectId}/library/raw?path=${path}`,
}))

vi.mock('../lib/libraryApi', () => ({
  uploadLibraryFile: vi.fn(),
  replaceLibraryFile: vi.fn(),
  libraryFileBytes: vi.fn(),
}))

const { listProjectLibrary } = await import('../api')

import ProjectLibraryView from './ProjectLibraryView.vue'

afterEach(cleanup)

// VOverlay / v-menu 定位要用到这几样，happy-dom 不给（同 ProjectLibraryView.spec.ts）。
beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
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

function makeFiles(n: number): LibraryFile[] {
  return Array.from({ length: n }, (_, i) => ({
    path: `报告-${i}.docx`,
    bytes: 2048,
    modified: 1758000000,
    added_by: 'alice',
    added_at: '2026-09-20T10:00:00Z',
    room: null,
    replaced: 0,
    references: 0,
  }))
}

const Blank = defineComponent({ render: () => h('div') })

async function mount(files: LibraryFile[]) {
  vi.mocked(listProjectLibrary).mockResolvedValue({ data: files, total: files.length })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/projects/:projectId/library', name: 'project-library', component: ProjectLibraryView, props: true },
      { path: '/projects/:projectId/topics/:topicId', name: 'workspace-topic', component: Blank },
    ],
  })
  await router.push('/projects/p1/library')
  await router.isReady()
  const Host = defineComponent({ setup: () => () => h(components.VApp, null, () => h(RouterView)) })
  const view = render(Host, {
    global: { plugins: [createVuetify({ components, directives }), router, createPinia()] },
  })
  await waitFor(() => expect(view.container.textContent).toContain(files[0]!.path))
  return view
}

const dataOf = (at: number) => virtua.seen.at(at)?.data as unknown[] | undefined

beforeEach(() => {
  setLocale('zh-CN')
  vi.clearAllMocks()
  virtua.seen.length = 0
})

describe('文件不到门槛', () => {
  it('整列画出来，一份不少，也不碰 virtua', async () => {
    const { container } = await mount(makeFiles(VIRTUAL_LIST_CONTENT_THRESHOLD - 1))
    expect(container.querySelectorAll('.library-row')).toHaveLength(VIRTUAL_LIST_CONTENT_THRESHOLD - 1)
    expect(virtua.seen).toHaveLength(0)
  })
})

describe('文件过了门槛', () => {
  it('整列交给 virtua（数据原样递过去），每一行仍是一个 li，操作按钮还在', async () => {
    const { container } = await mount(makeFiles(VIRTUAL_LIST_CONTENT_THRESHOLD + 50))
    // 滚动容器是模板 ref，挂完那一帧才落地（见 VirtualList 文件头），所以等它一拍。
    await waitFor(() => expect(virtua.seen).not.toHaveLength(0))
    expect(dataOf(0)).toHaveLength(VIRTUAL_LIST_CONTENT_THRESHOLD + 50)
    // 外壳还是 li：虚拟化只省屏幕外的节点，不从无障碍树上切掉列表语义。
    expect(virtua.seen[0]!.item).toBe('li')
    // 行本身没变：打开按钮和 ⋯ 操作都在。
    expect(container.querySelector('.library-row__open')).not.toBeNull()
    expect(container.querySelector('.library-row__name')).not.toBeNull()
  })
})
