/** 看板读失败：整块换成失败块（§3.10），给服务端原话和一条重试；绝不退回「暂无任务」。
 *
 *  以前这里是一行灰字加一颗按钮——它不像一块板坏了，倒像板在说一件小事。这一份钉
 *  的是它现在是那一块共同的失败块，以及重试真的再读一次。
 */
import type { Component } from 'vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const listProjectTasks = vi.fn()

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    listProjectTasks: (...a: unknown[]) => listProjectTasks(...a),
    listProjectArtifacts: () => Promise.resolve({ data: [], total: 0 }),
    getProjectSite: () => Promise.resolve({ site: null, source_revision: null, candidates: [], can_publish: false }),
  }
})

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useRoute: () => ({ query: {} }),
}))

// 话题也空着：这样「什么都没有」那一屏本来会画出来，用来证明失败块压过它。
vi.mock('@/stores/workspace', () => ({ useWorkspaceStore: () => ({ topics: [], members: [] }) }))

import RunningWorkView from './RunningWorkView.vue'

import { setLocale, t } from '@/i18n'

// 断言按中文文案写：默认 locale 是 en，这里钉回 zh-CN。
beforeEach(() => setLocale('zh-CN'))

const Board = RunningWorkView as unknown as Component

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  listProjectTasks.mockReset()
})

function mount() {
  return render(Board, { props: { projectId: 'p1' }, global: { plugins: [vuetify] } })
}

describe('看板读失败', () => {
  it('整块换成失败块：服务端原话 + 重试，不显示「暂无任务」', async () => {
    listProjectTasks.mockRejectedValue(new Error('HTTP 503 for /tasks'))
    const { findByText, queryByText } = mount()
    await findByText(t('work.board.loadFailed'))
    expect(queryByText('HTTP 503 for /tasks')).toBeTruthy()
    expect(queryByText(t('work.room.noTasks')), '失败不能显示成「暂无任务」').toBeNull()
    expect(queryByText(t('global.loadError.retry'))).toBeTruthy()
  })

  it('点重试，重新读一次', async () => {
    listProjectTasks.mockRejectedValueOnce(new Error('boom'))
    const { findByRole } = mount()
    const retry = await findByRole('button', { name: t('global.loadError.retry') })
    listProjectTasks.mockResolvedValue({ data: [], total: 0 })
    const before = listProjectTasks.mock.calls.length
    await fireEvent.click(retry)
    await waitFor(() => expect(listProjectTasks.mock.calls.length).toBeGreaterThan(before))
  })
})
