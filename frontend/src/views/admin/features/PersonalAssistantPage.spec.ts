/**
 * 个人芝士的功能数据页（`/admin/feature-stats/personal-assistant`）：授权判断的次数带着
 * 「直接执行 / 请用户确认」的拆分，没有分母的命中率画破折号不画 0，切窗口重新取数。
 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const report = vi.fn()
vi.mock('@/views/admin/features/featureApi', () => ({
  getPersonalAssistantReport: (...args: unknown[]) => report(...args),
}))

vi.mock('vue-i18n', async (importOriginal) => {
  const actual = await importOriginal<typeof import('vue-i18n')>()
  return {
    ...actual,
    useI18n: () => ({
      t: (key: string, params?: Record<string, unknown>) => (params ? `${key} ${JSON.stringify(params)}` : key),
    }),
  }
})

import PersonalAssistantPage from './PersonalAssistantPage.vue'

function fixture() {
  return {
    days: 30,
    gate: {
      checks: 40,
      allowed: 30,
      asked: 10,
      byStage: { 'allowed:quick': 28, 'allowed:reasoned': 2, 'asked:rule': 6, 'asked:reasoned': 4 },
      byKind: [{ kind: 'message', allowed: 25, asked: 3 }],
      inputTokens: 1000,
      cacheReadTokens: 800,
      cacheRate: 0.8,
      outputTokens: 50,
      costUsd: 0.0123,
      latencyMs: { count: 34, median: 420, p90: 900 },
    },
    answers: {
      answers: 0,
      calls: 0,
      inputTokens: 0,
      cacheReadTokens: 0,
      cacheRate: null,
      callCacheRate: { median: null, p10: null },
    },
    daily: [{ date: '2026-10-21', checks: 40, asked: 10, costUsd: 0.0123 }],
  }
}

function mount() {
  return render(PersonalAssistantPage, { global: { plugins: [createVuetify({ components, directives })] } })
}

beforeEach(() => {
  report.mockReset()
  report.mockResolvedValue(fixture())
})

describe('PersonalAssistantPage', () => {
  it('says how many checks there were and how they split', async () => {
    const view = mount()
    await waitFor(() =>
      expect(view.getByText('featureStats.assistant.kpi.checksSplit {"allowed":"30","asked":"10"}')).toBeTruthy()
    )
    expect(view.getByText('featureStats.assistant.kinds.row {"allowed":"25","asked":"3"}')).toBeTruthy()
  })

  it('a rate with nothing under it is a dash, not zero', async () => {
    const view = mount()
    await waitFor(() => expect(report).toHaveBeenCalled())
    await view.findByText('featureStats.assistant.kpi.answerCache')
    // 80% and 25% are on the page; a bare 0% would be the empty rate drawn as zero.
    expect(view.container.textContent).not.toMatch(/(^|[^0-9.])0(\.0)?%/)
  })

  it('switching the window asks the server again', async () => {
    const view = mount()
    await waitFor(() => expect(report).toHaveBeenCalledWith(30))
    await fireEvent.click(view.getByRole('tab', { name: 'featureStats.days.7' }))
    await waitFor(() => expect(report).toHaveBeenCalledWith(7))
  })
})
