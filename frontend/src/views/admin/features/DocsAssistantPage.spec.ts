/**
 * 「问芝士」的功能数据页（`/admin/feature-stats/docs-assistant`）。
 *
 * 这一页上「数字说错话」的坏法比「画不出来」多，所以断言的每一条都对着一种读错：
 *
 * * **比例要和分母一起说**：12 人提问、占登录访客 3.9% —— 只画那个 12，访客掉一半
 *   这件事在页面上看不出来。
 * * **「没读到」和「是零」长得不一样**：花费读不到价目表时是长破折号，不是 $0（0 读作
 *   「免费」，那是另一个意思），标签也换成「估算不了」。
 * * **窗口是服务端的问法**：切页签要**重新取数**（带 `days`），不是在本地筛已到的行。
 * * **答不上来那张表里没有提问者**（同 `AdminQuestionTable.spec.ts` 的理由）。
 * * **读失败不画成一片零**：拿不到报告时画错误态，不画一排 0（那会被读成「这个月没人
 *   用过」）。
 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const report = vi.fn()
vi.mock('@/views/admin/features/featureApi', () => ({
  getDocsAssistantReport: (...args: unknown[]) => report(...args),
}))

// 键名透传 + 参数照抄：句子里那个数（占比、人均）才是要断言的东西。
vi.mock('vue-i18n', async (importOriginal) => {
  const actual = await importOriginal<typeof import('vue-i18n')>()
  return {
    ...actual,
    useI18n: () => ({
      t: (key: string, params?: Record<string, unknown>) => (params ? `${key} ${JSON.stringify(params)}` : key),
    }),
  }
})

import DocsAssistantPage from './DocsAssistantPage.vue'

/** 一份「像真的」的报告：每个数都挑成一眼能认出来的那种（1,234 / 3.9% / $1.23）。 */
function fixture(over: Record<string, unknown> = {}) {
  return {
    id: 'docs-assistant',
    title: '问芝士',
    summary: '文档站的问答助手',
    days: 30,
    start: '2026-08-31',
    end: '2026-09-29',
    numbers: {
      visitors: { value: 1234, logged_in: 312 },
      askers: { value: 12, share: 0.0385 },
      questions: { value: 96, per_asker: 8 },
      answer_rate: { value: 0.875, answered: 84, total: 96 },
      cost: { usd: 1.2345, per_question: 0.012859375, source: 'estimated', unpriced_tokens: 0 },
    },
    trend: [
      { date: '2026-09-01', visitors: 10, askers: 1, questions: 2 },
      { date: '2026-09-02', visitors: 12, askers: 0, questions: 0 },
    ],
    tokens: {
      count: 96,
      avg: 3000.4,
      median: 2800,
      min: 120,
      p90: 9000,
      max: 21000,
      histogram: [
        { from: 0, to: 2100, count: 3 },
        { from: 2100, to: 4200, count: 1 },
      ],
    },
    latency: { count: 96, avg: 1500, median: 900, min: 60, p90: 4000, max: 12000 },
    outcomes: { answered: 84, no_match: 9, failed: 3, total: 96 },
    unanswered: [{ question: '怎么把项目导出成压缩包？', page: 'quickstart', count: 4 }],
    ...over,
  }
}

function mountPage() {
  return render(DocsAssistantPage, {
    global: {
      plugins: [createVuetify({ components, directives })],
      // 口径注平时收在 tooltip 里，而 happy-dom 打开 tooltip 会炸在 `visualViewport`
      // 上；这里把它摊成一行文字 —— 这一页要断言的正是「那句话说了什么」。
      stubs: {
        AdminNoteTip: { props: ['text'], template: '<span class="notetext">{{ text }}</span>' },
      },
    },
  })
}

beforeEach(() => {
  report.mockReset()
})

describe('问芝士的功能数据页', () => {
  it('窗口内的关键数字按口径画出来，比例带着分母', async () => {
    report.mockResolvedValue(fixture())
    const { findByText, container } = mountPage()
    // 等的是**到货之后**才有的那个数，不是那行标题：标题在骨架那一帧就已经在页面上
    // 了（`findByText` 会立刻返回），报告还没落地就去读 textContent 会读到一排空骨架。
    await findByText('1,234')

    const text = container.textContent ?? ''
    // 访客 / 其中登录 / 提问（千分位）。
    expect(text).toContain('1,234')
    expect(text).toContain('312')
    expect(text).toContain('96')
    // 提问者的占比（分母是登录访客）与访客里登录的比例，两句都带着分母。
    expect(text).toContain('featureStats.kpi.askersShare {"percent":"3.9%"}')
    expect(text).toContain('featureStats.kpi.loggedInShare {"percent":"25%"}')
    // 回答成功率：84 / 96，同时给比例和两个数。
    expect(text).toContain('88%')
    expect(text).toContain('featureStats.kpi.answeredOf {"answered":"84","total":"96"}')
    // 人均提问次数。
    expect(text).toContain('featureStats.kpi.perAsker {"value":"8.0"}')
    // 统计窗口那行：两端的日期。
    expect(text).toContain('featureStats.page.window {"start":"2026-08-31","end":"2026-09-29"}')
    // 花费是**估算**，标签必须说出来；每题均摊跟着走。
    expect(text).toContain('featureStats.kpi.cost')
    expect(text).toContain('$1.23')
    expect(text).toContain('featureStats.kpi.perQuestion {"value":"$0.0129"}')
  })

  it('读不到价目表时花费是破折号加「估算不了」，不是 $0', async () => {
    report.mockResolvedValue(
      fixture({
        numbers: {
          ...fixture().numbers,
          cost: { usd: null, per_question: null, source: 'unavailable', unpriced_tokens: 1200 },
        },
      })
    )
    const { findByText, container } = mountPage()
    await findByText('featureStats.kpi.costUnavailable')

    const text = container.textContent ?? ''
    expect(text).not.toContain('$0')
    // 「为什么没有数」那句话在花费那张卡的口径注里（见 `mountPage` 的 stub）。
    expect(text).toContain('featureStats.kpi.costUnknownNote')
    // 那一格的数是长破折号（不是 0）。
    expect(container.querySelectorAll('.akpi__num').length).toBeGreaterThan(0)
    expect(Array.from(container.querySelectorAll('.akpi__num')).some((el) => el.textContent?.trim() === '—')).toBe(true)
  })

  it('切窗口是重新取数（带 days），不是在本地筛', async () => {
    report.mockResolvedValue(fixture())
    const { findByText, getAllByRole } = mountPage()
    await findByText('featureStats.trend.title')
    expect(report).toHaveBeenCalledWith(30)

    const seven = getAllByRole('tab').find((tab) => tab.textContent?.includes('featureStats.days.7'))
    expect(seven).toBeTruthy()
    await fireEvent.click(seven as HTMLElement)

    await waitFor(() => expect(report).toHaveBeenCalledWith(7))
  })

  it('token 与耗时两组分布都画出来，token 那一组带直方图', async () => {
    report.mockResolvedValue(fixture())
    const { findByText, container } = mountPage()
    await findByText('2,800')

    const ranges = Array.from(container.querySelectorAll('.ahist__range')).map((el) => el.textContent?.trim())
    expect(ranges).toEqual(['0–2,100', '2,100–4,200'])
    const text = container.textContent ?? ''
    // token 那一组：平均是浮点（3000.4），四舍五入到整数再分组。
    expect(text).toContain('3,000')
    expect(text).toContain('2,800')
    expect(text).toContain('21,000')
    // 耗时那一组：单位跟着量级走（900 毫秒写 ms，4 秒写 s），不写成一串五位数。
    expect(text).toContain('900\u00a0ms')
    expect(text).toContain('4.0\u00a0s')
  })

  it('答不上来的问题画在表里，表里没有提问者', async () => {
    report.mockResolvedValue(fixture())
    const { findByText, container } = mountPage()
    await findByText('怎么把项目导出成压缩包？')

    expect(container.textContent).toContain('怎么把项目导出成压缩包？')
    expect(container.textContent).toContain('quickstart')
    // 只取这张表自己的表头：页面上还有别的表（折线的数据表孪生体）也有 `th`。
    const heads = Array.from(container.querySelectorAll('.aqt__th')).map((el) => el.textContent?.trim())
    expect(heads).toEqual([
      'featureStats.unanswered.column.question',
      'featureStats.unanswered.column.page',
      'featureStats.unanswered.column.count',
    ])
  })

  it('答完的结果三档都在（分母不藏起来）', async () => {
    report.mockResolvedValue(fixture())
    const { findByText, container } = mountPage()
    await findByText('featureStats.outcomes.answered')

    const text = container.textContent ?? ''
    expect(text).toContain('featureStats.outcomes.answered')
    expect(text).toContain('featureStats.outcomes.noMatch')
    expect(text).toContain('featureStats.outcomes.failed')
  })

  it('读失败画错误态，不画一排 0', async () => {
    report.mockRejectedValueOnce(new Error('boom'))
    const { findByText, queryByText } = mountPage()

    expect(await findByText('featureStats.page.loadFailed')).toBeTruthy()
    // 一排 0 会被读成「这个月没人用过」，比一句「读不到」糟得多。
    expect(queryByText('featureStats.trend.title')).toBeNull()
  })
})
