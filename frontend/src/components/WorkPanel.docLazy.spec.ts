// 文档那一格是按需取的（`WorkPanel` 里 `defineAsyncComponent` 取 `PanelDocHost`，那一坨
// 压缩后约 1.1 MB，不能钉在房间的首屏上）。代价是它不再是「这一刻就在」：冷开时点文档卡
// 上的「查看改动」，面板切过去了，编辑器那一块还在网络上，模板 ref 还没接上——那时
// `docRefs.get(...)?.reviewEdits(...)` 那一句会被 `?.` 静默吞掉，高亮什么也不发生，而且
// 没有任何报错。这一份就钉这一件事：那一块到得晚，晚之前递过去的「查看改动」也得算数。
import type { Component } from 'vue'
import type { Topic } from '@/cx_types'
import type { DocReviewRequest, OpenedDocument } from '@/lib/docReview'

import { defineComponent, h } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render, screen } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import i18n, { setLocale } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))

const workSummary = vi.fn()
const roomTasks = vi.fn()

vi.mock('../api', () => ({
  getPreview: vi.fn(async () => null),
  getTopicWorkSummary: (...a: unknown[]) => workSummary(...a),
  listRoomTasks: (...a: unknown[]) => roomTasks(...a),
}))

vi.mock('../api/routines', () => ({
  listProjectRoutines: vi.fn(async () => ({ data: [], total: 0 })),
  getRoutine: vi.fn(),
  createRoutine: vi.fn(),
  updateRoutine: vi.fn(),
  routineAction: vi.fn(),
  deleteRoutine: vi.fn(),
}))

// 按需取的那一块：闸门捏在这里，测的就是「它还没到」和「它到了」这两头。闸门是真异步
// 的一跳（外面捏着的一个 promise），不是 `nextTick` —— 它模拟的是网络上还要走一遭的那
// 个 chunk，而同一轮里的一个微任务快得不像。
const host = vi.hoisted(() => {
  let release!: () => void
  return {
    arrives: new Promise<void>((resolve) => (release = resolve)),
    release: () => release(),
    calls: [] as string[],
    // 这一块是什么时候被要的：`defineAsyncComponent` 只在这一格真要画时才问它，静态 import
    // 则是导入 `WorkPanel` 本身时就问。下面第一条就钉这个差别。
    asked: false,
  }
})

// 闸门最多捏 2 秒。`WorkPanel` 被改回静态 import 的话，这个工厂在收集模块时就会被叫
// 起来，而闸门要到用例里才有人放——整份文件会停在这儿，不报错也不结束，CI 只看到一个
// 发呆的进程。给个上限让它放行：那一刻 `host.asked` 已经是 true，下面第一条就以断言
// 失败收场，看得见红的是哪一条。（这个秒数得写死在工厂里：`vi.mock` 的工厂被提到文件
// 最上面，外面那个常量这会儿还没初始化。）
vi.mock('./work/PanelDocHost.vue', async () => {
  host.asked = true
  await Promise.race([host.arrives, new Promise((r) => setTimeout(r, 2_000))])
  const { defineComponent, h } = await import('vue')
  return {
    // `__esModule` 是给 Vue 的异步组件看的，见 `TaskOverview.opening.spec.ts` 里同一处。
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
  workSummary.mockResolvedValue({ has_run: false, changed_files: [] })
  roomTasks.mockResolvedValue({ data: [], total: 0 })
  // 这一格收到的调用记在同一个数组里，跨用例留着：「这一次一条都不该有」得从头数。
  host.calls.length = 0
})

/** 面板 `defineExpose` 出来的那几样，得从外面拿着它调 —— 就像 `TopicView` 那样。 */
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

describe('文档那一格（按需取）', () => {
  it('面板自己先挂上时，编辑器那一块还没被要过', async () => {
    // 这一条钉的是「按需取」这条边界本身：`WorkPanel` 改回静态 import 的话，那一坨 1.1 MB
    // 在这次断言之前就已经在模块图里了，`asked` 是 true。别的活没人守着这件事 —— 改回静态
    // import 只有这一条会红，而且红在断言上（闸门有上限，见上面 `HOLD_MS`）。
    // 任务页那一侧（`TaskOverview`）同样的边界在 `TaskOverview.opening.spec.ts` 里。
    expect(host.asked).toBe(false)
    mountPanel()
    await flush()
    expect(host.asked).toBe(false)
  })

  it('那一块还在路上时点「查看改动」：到了也得收到', async () => {
    const { panel } = mountPanel()
    await flush()

    await panel.openDocument({ id: 'doc-1', title: '说明' }, REVIEW)
    await flush()
    // 编辑器那一块还没到：这会儿它连挂都没挂上，谁也没得点亮。
    expect(screen.queryByTestId('doc-host')).toBeNull()
    expect(host.calls).toEqual([])

    host.release()
    await flush()
    expect(screen.queryByTestId('doc-host')).toBeTruthy()

    expect(host.calls).toContain('review')
  })
})
