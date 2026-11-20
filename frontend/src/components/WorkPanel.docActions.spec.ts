// 自由区里打开的资料库文档，「⋯」里有「分享… / 移动到… / 删除文档」。面板这一层要把它们
// 接到和资料库那一页同一套框上：点了得有框出来，删除答应了文档就没了、那一格也关掉，
// 取消了什么都不动。以前这一层没接这三条线，点下去什么也不发生。
//
// 文档那一格本身（编辑器、顶栏的 ⋯ 菜单）换成一个替身：它对外的约定就是发出 `share` /
// `move` / `delete` 三个事件，这里测的是面板收到之后用户看得到的结果。
import type { Component } from 'vue'
import type { Topic } from '@/cx_types'
import type { OpenedDocument } from '@/lib/docReview'

import { defineComponent, h } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import i18n, { setLocale, t } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))

vi.mock('../api', () => ({
  getPreview: vi.fn(async () => null),
  getTopicWorkSummary: vi.fn(async () => ({ has_run: false, changed_files: [] })),
  listRoomTasks: vi.fn(async () => ({ data: [], total: 0 })),
}))

vi.mock('../api/routines', () => ({
  listProjectRoutines: vi.fn(async () => ({ data: [], total: 0 })),
  getRoutine: vi.fn(),
  createRoutine: vi.fn(),
  updateRoutine: vi.fn(),
  routineAction: vi.fn(),
  deleteRoutine: vi.fn(),
}))

// 后端的替身：资料库里有哪几份文档。删除从这里拿走，断言看它最后剩什么。
const library = vi.hoisted(() => ({ docs: new Map<string, string>() }))

vi.mock('../api/projectDocuments', async (importOriginal) => ({
  ...(await importOriginal<object>()),
  deleteDocument: async (id: string) => {
    library.docs.delete(id)
  },
  getDocumentAbout: async (id: string) => ({
    id,
    title: library.docs.get(id) ?? '',
    team_id: 1,
    project_id: 'p1',
  }),
}))

vi.mock('../api/documentShares', async (importOriginal) => ({
  ...(await importOriginal<object>()),
  getDocumentShare: async () => ({ may_share: true, link: null }),
}))

vi.mock('../api/teamDocuments', async (importOriginal) => ({
  ...(await importOriginal<object>()),
  getDocumentPlaces: async () => ({ places: [] }),
}))

// 文档那一格的替身：顶栏 ⋯ 里那三项，点了就发出和真的一样的事件。
vi.mock('./work/PanelDocHost.vue', async () => {
  const { defineComponent, h } = await import('vue')
  const { t } = await import('@/i18n')
  return {
    __esModule: true,
    default: defineComponent({
      name: 'PanelDocHostStub',
      emits: ['share', 'move', 'delete'],
      setup(_props, { emit }) {
        return () =>
          h('div', { 'data-testid': 'doc-host' }, [
            h('button', { onClick: () => emit('share') }, t('work.room.doc.share')),
            h('button', { onClick: () => emit('move') }, t('work.room.doc.move')),
            h('button', { onClick: () => emit('delete') }, t('work.room.doc.delete')),
          ])
      },
    }),
  }
})

import WorkPanel from './WorkPanel.vue'

const Panel = WorkPanel as unknown as Component

const topic = {
  id: 't1',
  project_id: 'p1',
  parent_id: null,
  title: 't',
  kind: 'topic',
  status: 'active',
  created_by: 'u',
  created_at: '2026-08-18T00:00:00Z',
  updated_at: '2026-08-18T00:00:00Z',
} as Topic

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
})

beforeEach(() => {
  library.docs = new Map([['doc-1', '说明']])
  // 框是 Vuetify 的浮层，定位时要读它；happy-dom 没有。
  vi.stubGlobal('visualViewport', new EventTarget())
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

async function flush() {
  for (let i = 0; i < 12; i += 1) await new Promise((r) => setTimeout(r, 0))
}

/** 挂上面板，在自由区打开资料库里的「说明」。 */
async function openDocInPanel() {
  let panel: { openDocument: (d: OpenedDocument) => Promise<void> } | null = null
  const Harness = defineComponent({
    setup: () => () =>
      h(Panel, {
        topic,
        activityTick: 0,
        ref: (el: unknown) => {
          panel = el as typeof panel
        },
      }),
  })
  render(Harness, {
    global: {
      plugins: [vuetify, i18n],
      stubs: {
        PanelSiteHost: true,
        PanelChangesHost: true,
        PanelPreviewHost: true,
        RoutinePanelHost: true,
      },
    },
  })
  await flush()
  await panel!.openDocument({ id: 'doc-1', title: '说明' })
  await flush()
  expect(screen.getByTestId('doc-host')).toBeTruthy()
}

const deleteTitle = () => t('work.library.deleteTitle', { name: '说明' })

describe('自由区里的资料库文档：⋯ 里的分享、移动、删除', () => {
  it('删除文档：先问一句，答应了文档就删了，那一格也关掉', async () => {
    await openDocInPanel()

    await fireEvent.click(screen.getByRole('button', { name: t('work.room.doc.delete') }))
    await flush()
    const confirm = await screen.findByRole('alertdialog', { name: deleteTitle() })

    await fireEvent.click(within(confirm).getByRole('button', { name: t('work.library.delete') }))
    await flush()

    expect(library.docs.has('doc-1')).toBe(false)
    expect(screen.queryByTestId('doc-host')).toBeNull()
  })

  it('删除文档时取消：文档还在，那一格也还开着', async () => {
    await openDocInPanel()

    await fireEvent.click(screen.getByRole('button', { name: t('work.room.doc.delete') }))
    await flush()
    const confirm = await screen.findByRole('alertdialog', { name: deleteTitle() })

    await fireEvent.click(within(confirm).getByRole('button', { name: t('global.cancel') }))
    await flush()

    expect(library.docs.has('doc-1')).toBe(true)
    expect(screen.getByTestId('doc-host')).toBeTruthy()
  })

  it('分享…：打开分享这份文档的框', async () => {
    await openDocInPanel()

    await fireEvent.click(screen.getByRole('button', { name: t('work.room.doc.share') }))
    await flush()

    expect(await screen.findByText(t('work.room.doc.shareDialog.title', { name: '说明' }))).toBeTruthy()
  })

  it('移动到…：打开挪这份文档的框', async () => {
    await openDocInPanel()

    await fireEvent.click(screen.getByRole('button', { name: t('work.room.doc.move') }))
    await flush()

    expect(await screen.findByText(t('work.room.doc.moveDialog.title', { name: '说明' }))).toBeTruthy()
  })
})
