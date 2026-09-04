// 「预览」这一格在一条支线上也要有。
//
// 预览属于**地点**：`cheese artifact` / `cheese serve` 都按地点记，一条支线拿出来
// 给人看的是它自己那份结果。这一份守的是「给不给这一格」只看这个地点有没有东西可
// 看，不看它是房间还是支线。
//
// 顺带守住旁边那一条**没有**跟着变的规矩：「改动」属于树，一条支线和它的同伴写的
// 是同一条分支，所以支线上仍然不给「改动」那一格。两条判断相邻、长得几乎一样，最
// 容易在改一条的时候把另一条一起带走。
import type { Component } from 'vue'
import type { Topic } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getPreview = vi.fn()
const getTopicWorkSummary = vi.fn()

vi.mock('../api', () => ({
  getPreview: (...args: unknown[]) => getPreview(...args),
  getTopicWorkSummary: (...args: unknown[]) => getTopicWorkSummary(...args),
}))

import WorkPanel from './WorkPanel.vue'

const Panel = WorkPanel as unknown as Component

const base = {
  id: 't1',
  project_id: 'p1',
  parent_id: null,
  title: 't',
  kind: 'topic',
  status: 'active',
  created_by: 'u',
  created_at: '2026-09-03T00:00:00Z',
  updated_at: '2026-09-03T00:00:00Z',
} as Topic

/** 一条支线在前端就是 `kind: 'thread'`、`parent_id` 指着它房间的那种地点。 */
const thread = { ...base, id: 'k1', parent_id: 't1', kind: 'thread' } as Topic

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
  vi.stubGlobal('fetch', async () => ({ ok: true, json: async () => ({}), text: async () => '' }))
  getPreview.mockReset()
  getTopicWorkSummary.mockReset()
  getPreview.mockResolvedValue(null)
  getTopicWorkSummary.mockResolvedValue({ has_run: false, changed_files: [] })
})

function mount(topic: Topic) {
  return render(Panel, {
    props: { topic, activityTick: 0, withChat: false },
    global: {
      plugins: [vuetify],
      // 四个子面板各自会去拿数据/建编辑器，这一份只关心 tab 栏本身。
      stubs: { PanelDoc: true, PanelSite: true, PanelChanges: true, PanelPreview: true },
    },
  })
}

/** 这个地点点名了一份 artifact —— 面板靠这个指针决定要不要摆出「预览」。 */
function pointsAtSomething() {
  getPreview.mockResolvedValue({ kind: 'file', path: 'r.html', mime: 'text/html', artifact_id: 'a1' })
}

describe('预览那一格', () => {
  it('支线点名了东西，就有这一格', async () => {
    pointsAtSomething()
    const { findByRole } = mount(thread)
    expect(await findByRole('tab', { name: /预览/ })).toBeTruthy()
  })

  it('房间照旧有这一格', async () => {
    pointsAtSomething()
    const { findByRole } = mount(base)
    expect(await findByRole('tab', { name: /预览/ })).toBeTruthy()
  })

  it('支线没点名任何东西，就没有这一格', async () => {
    // 判据是「这个地点有没有东西可看」，不是「它是不是支线」。
    getTopicWorkSummary.mockResolvedValue({ has_run: true, changed_files: [] })
    const { findByRole, queryByRole } = mount(thread)
    // 有两格以上 tab 栏才出现，所以先让「现场」把它显形。
    expect(await findByRole('tab', { name: /现场/ })).toBeTruthy()
    expect(queryByRole('tab', { name: /预览/ })).toBeNull()
  })

  it('支线上仍然没有「改动」那一格', async () => {
    // 改动属于树：一条支线和它的同伴写的是同一条分支，那份 diff 是他们一起做的。
    // 两个 mock 都要在 mount 之前摆好 —— 面板一挂上就去问了，之后再改没人会再问
    // 一次，那样这条断言会因为「本来就没有改动」而假绿。
    pointsAtSomething()
    getTopicWorkSummary.mockResolvedValue({ has_run: true, changed_files: ['a.ts'] })
    const { findByRole, queryByRole } = mount(thread)
    expect(await findByRole('tab', { name: /预览/ })).toBeTruthy()
    expect(queryByRole('tab', { name: /改动/ })).toBeNull()
  })
})
