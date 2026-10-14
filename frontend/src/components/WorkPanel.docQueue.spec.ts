// 「查看改动」递过去时编辑器那一块还在路上，就先记着——这一份测的是**记着的那一次什么
// 时候该作废**：那一格还没挂上就被关掉，那一次不该留到下次开同一份文档时突然亮。
//
// 和 `WorkPanel.docLazy.spec.ts` 分开一份文件是因为闸门只能拦一次：那一份的第二个用例把
// 闸门放开了，往后这个模块就一直挂着，再没有「还在路上」这个状态可测。两个文件各有一份
// 模块注册表，各自捏一道闸门。
import type { Component } from 'vue'
import type { Topic } from '@/cx_types'
import type { DocReviewRequest, OpenedDocument } from '@/lib/docReview'

import { defineComponent, h } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import i18n, { setLocale } from '@/i18n'

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

const host = vi.hoisted(() => {
  let release!: () => void
  return {
    arrives: new Promise<void>((resolve) => (release = resolve)),
    release: () => release(),
    calls: [] as string[],
  }
})

vi.mock('./work/PanelDocHost.vue', async () => {
  // 闸门最多捏 2 秒。`WorkPanel` 被改回静态 import 的话，这个工厂在收集模块时就会被叫
  // 起来，而闸门要到用例里才有人放——整份文件会停在这儿，不报错也不结束，CI 只看到一个
  // 发呆的进程（实测 120 秒超时、没有任何输出）。给个上限让它放行，红就落在断言上：
  // 那一块已经挂上了，第一条 `expect(host.calls).toEqual([])` 失败。秒数得写死在工厂里，
  // `vi.mock` 的工厂被提到文件最上面，外面那个常量这会儿还没初始化。同一个道理见
  // `WorkPanel.docLazy.spec.ts`。
  await Promise.race([host.arrives, new Promise((r) => setTimeout(r, 2_000))])
  const { defineComponent, h } = await import('vue')
  return {
    __esModule: true,
    default: defineComponent({
      name: 'PanelDocHostStub',
      setup(_props, { expose }) {
        expose({
          pulse: () => host.calls.push('pulse'),
          highlightTurn: (turnId: string) => host.calls.push(`turn:${turnId}`),
          reviewEdits: () => host.calls.push('review'),
        })
        return () => h('div', { 'data-testid': 'doc-host' })
      },
    }),
  }
})

import WorkPanel from './WorkPanel.vue'

const Panel = WorkPanel as unknown as Component

interface PanelApi {
  openDocument: (document: OpenedDocument, review?: DocReviewRequest) => Promise<void>
}

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

const REVIEW: DocReviewRequest = {
  requester: '芝士',
  edits: [{ old: '30 天', new: '60 天' }],
}

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
  host.calls.length = 0
})

function mountPanel(): { panel: PanelApi } {
  let panel: PanelApi | null = null
  const Harness = defineComponent({
    setup: () => () =>
      h(Panel, {
        topic,
        activityTick: 0,
        ref: (el: unknown) => {
          panel = el as PanelApi | null
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
  return {
    get panel(): PanelApi {
      return panel as PanelApi
    },
  }
}

async function flush() {
  for (let i = 0; i < 12; i += 1) await new Promise((r) => setTimeout(r, 0))
}

describe('编辑器那一块还在路上时关掉那一格', () => {
  it('没递出去的「查看改动」跟着作废：下次开同一份，那几处不会突然亮', async () => {
    const { panel } = mountPanel()
    await flush()

    await panel.openDocument({ id: 'doc-1', title: '说明' }, REVIEW)
    await flush()
    // 编辑器那一块还没到，那一次先记着，一个人也没收到。
    expect(host.calls).toEqual([])

    // 人嫌慢，把那一格关了。
    await fireEvent.click(screen.getByLabelText('关闭 说明'))
    await flush()

    // 那一块现在到了，可那一格已经不在了。
    host.release()
    await flush()
    expect(host.calls).toEqual([])

    // 再开同一份文档，这一次没有人要「查看改动」——上一次那一次不该跟过来。
    await panel.openDocument({ id: 'doc-1', title: '说明' })
    await flush()

    expect(screen.queryByTestId('doc-host')).toBeTruthy()
    expect(host.calls).toEqual([])
  })
})
