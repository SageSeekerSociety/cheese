// 文档存回之后，文档那一格重读已存的那一版（最近一次编辑），编辑器里的段落不该因此
// 重建：正文是协同文档本身，重读的只是围着它的那几样。
import type { Component } from 'vue'
import type { Topic } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getDocVersions = vi.fn()

// The document's version history: the last edit is read on open; none here.
vi.mock('../../api/docThreads', () => ({
  listDocThreads: async () => ({ data: [], total: 0 }),
  writeDocThread: async () => ({}),
}))
vi.mock('../../api/docHistory', () => ({
  getDocVersions: (...a: unknown[]) => getDocVersions(...a),
  restoreDocVersion: async () => ({}),
}))
vi.mock('../../api/docCollab', async () => ({
  ...(await vi.importActual<typeof import('../../api/docCollab')>('../../api/docCollab')),
  // 测试里房间的文档就用房间的 id 来认：fakeDocCollab 按它预置文档。
  getRoomDocument: async (topicId: string) => ({ id: topicId }),
}))
vi.mock('../../composables/useDocCollab', async () => ({
  useDocCollab: (await import('../../test/fakeDocCollab')).useFakeDocCollab,
}))

import { resetRooms, seedRoom } from '../../test/fakeDocCollab'

import PanelDocHost from './PanelDocHost.vue'

import { setLocale } from '@/i18n'

// 断言按中文文案写：默认 locale 是 en，这里钉回 zh-CN。
beforeEach(() => setLocale('zh-CN'))

const Doc = PanelDocHost as unknown as Component

const topic = {
  id: 't1',
  project_id: 'p1',
  parent_id: null,
  title: '房间',
  kind: 'topic',
  status: 'active',
  created_by: 'u',
  created_at: '2026-09-01T00:00:00Z',
  updated_at: '2026-09-01T00:00:00Z',
} as Topic

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  resetRooms()
  getDocVersions.mockReset()
  getDocVersions.mockResolvedValue({ versions: [], cursor: null })
})

/** 找到正文里含这段字的那个顶层元素。 */
function para(container: HTMLElement, text: string): HTMLElement {
  const hit = Array.from(container.querySelectorAll('.doc-prose > *')).find((el) => el.textContent?.includes(text))
  if (!hit) throw new Error(`找不到含「${text}」的段落`)
  return hit as HTMLElement
}

describe('文档存回之后重读已存的那一版时', () => {
  it('编辑器里的段落原样不动', async () => {
    const same = '# 标题\n\n第一段\n'
    seedRoom(topic.id, same)
    const { container, rerender } = render(Doc, {
      props: { topic, activityTick: 0, topicList: [] },
      global: { plugins: [vuetify] },
    })
    await waitFor(() => expect(container.textContent).toContain('第一段'))
    const before = para(container as HTMLElement, '第一段')

    seedRoom(topic.id, same)
    await rerender({ topic, activityTick: 1, topicList: [] })
    await waitFor(() => expect(getDocVersions).toHaveBeenCalledTimes(2))

    expect(para(container as HTMLElement, '第一段')).toBe(before)
  })
})
