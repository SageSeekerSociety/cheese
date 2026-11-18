// @vitest-environment jsdom
// 「领取者」页签那一层取数的一半：确认「重新评测全部最新提交」以后调重新评测的接口，
// 说排上了几版，再重读名单和「测试点」那一列。画面那一半在 `RosterView.spec.ts`。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor, within } from '@testing-library/vue'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import i18n from '@/i18n'

const api = vi.hoisted(() => ({
  getParticipants: vi.fn(),
  getSubmissionQueue: vi.fn(),
  summaries: vi.fn(),
  rejudge: vi.fn(),
  success: vi.fn(),
  info: vi.fn(),
  error: vi.fn(),
}))

vi.mock('vue-router', () => ({ useRoute: () => ({ params: { spaceId: '7' } }) }))
vi.mock('@/api/taskBoard', () => ({ TaskBoardApi: { getParticipants: api.getParticipants } }))
vi.mock('@/api/spaces', () => ({ SpacesApi: { getSubmissionQueue: api.getSubmissionQueue } }))
vi.mock('@/api/taskJudge', () => ({
  TaskJudgeApi: { SUMMARY_BATCH: 200, summaries: api.summaries, rejudge: api.rejudge },
}))
vi.mock('@/lib/toast', () => ({ toast: { success: api.success, info: api.info, error: api.error } }))

import Roster from './Roster.vue'

const t = (key: string, named?: Record<string, unknown>) => i18n.global.t(key, named ?? {})

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
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

// 整页挂上 Vuetify 的对话框，机器忙时一次要好几秒：放宽单个用例的时限。
describe('重新评测全部最新提交', { timeout: 20_000 }, () => {
  it('确认以后调接口、说排上了几版，再重读「测试点」那一列', async () => {
    api.getParticipants.mockResolvedValue({
      participants: [
        {
          id: 1,
          member: { id: 101, name: '林小满' },
          createdAt: 0,
          updatedAt: 0,
          deadline: null,
          approved: 'APPROVED',
        },
      ],
    })
    api.getSubmissionQueue.mockResolvedValue({
      submissions: [{ id: 501, participantId: 1, version: 1, createdAt: 0, review: null }],
    })
    api.summaries.mockResolvedValue({ summaries: [] })
    api.rejudge.mockResolvedValue({ queued: 3, reused: 1, unchanged: 0, skipped: 0 })

    const view = render(Roster, {
      props: { taskData: { id: 42, submitterType: 'USER', testSuite: [{ stage: null, cases: [] }] } as never },
      global: { plugins: [createVuetify({ components, directives }), i18n] },
    })

    await fireEvent.click(await view.findByTestId('roster-rejudge'))
    const dialog = await waitFor(() => {
      const el = document.querySelector('.v-overlay--active .v-card') as HTMLElement | null
      expect(el).not.toBeNull()
      return el!
    })
    expect(api.summaries).toHaveBeenCalledTimes(1)
    await fireEvent.click(within(dialog).getByRole('button', { name: t('tasks.judge.rejudge.confirm') }))

    await waitFor(() => expect(api.rejudge).toHaveBeenCalledWith(42, undefined))
    await waitFor(() =>
      expect(api.success).toHaveBeenCalledWith(t('tasks.judge.rejudge.done', { queued: 3, reused: 1 }))
    )
    await waitFor(() => expect(api.summaries).toHaveBeenCalledTimes(2))
  })

  it('接口拒了：照原样说出后端那句话', async () => {
    api.getParticipants.mockResolvedValue({
      participants: [
        {
          id: 1,
          member: { id: 101, name: '林小满' },
          createdAt: 0,
          updatedAt: 0,
          deadline: null,
          approved: 'APPROVED',
        },
      ],
    })
    api.getSubmissionQueue.mockResolvedValue({ submissions: [] })
    api.summaries.mockResolvedValue({ summaries: [] })
    api.rejudge.mockRejectedValue(new Error('没有权限'))

    const view = render(Roster, {
      props: { taskData: { id: 42, submitterType: 'USER', testSuite: [{ stage: null, cases: [] }] } as never },
      global: { plugins: [createVuetify({ components, directives }), i18n] },
    })
    await fireEvent.click(await view.findByTestId('roster-rejudge'))
    const dialog = await waitFor(() => {
      const el = document.querySelector('.v-overlay--active .v-card') as HTMLElement | null
      expect(el).not.toBeNull()
      return el!
    })
    await fireEvent.click(within(dialog).getByRole('button', { name: t('tasks.judge.rejudge.confirm') }))
    await waitFor(() => expect(api.error).toHaveBeenCalledTimes(1))
    expect(api.success).not.toHaveBeenCalled()
  })
})
