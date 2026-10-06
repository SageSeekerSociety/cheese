/**
 * 智能命名的功能数据页（`/admin/feature-stats/task-naming`）。
 *
 * 这一页每一处要断言的都是「数字会说谎」的那一种读法：
 *
 * * **比例和它的分母一起画**：「12 个房间被改掉」和「占被命名过的 48 个房间的 25%」
 *   是两句不同的话，少一句就读不出来命名到底好不好用。
 * * **读不到网关不是零**：花费是长破折号加一句「读不到」，不是 $0（0 读作「不花钱」），
 *   同一次读不到也让调用数与成功率一起变成破折号。
 * * **网关答了话、上面没有那把密钥是第三种**：说的是「没有这把密钥」，和「读不到」
 *   分开画，同样不报 0 —— 没有密钥就没有它的账，报零读起来是「命名一分钱没花」。
 * * **调用成功率不是命名成功率**：这一格的标签和口径注必须说的是网关那个口径，免得读
 *   的人把它当成模型质量。
 * * **窗口是服务端的问法**：切页签要**重新取数**（带 `days`），不是在本地筛已到的行。
 * * **读失败不画成一片零**：拿不到报告时画错误态，不画一排 0。
 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const report = vi.fn()
vi.mock('@/views/admin/features/featureApi', () => ({
  getTaskNamingReport: (...args: unknown[]) => report(...args),
}))

// 键名透传 + 参数照抄：句子里那些数（分母、拆分）才是要断言的东西。
vi.mock('vue-i18n', async (importOriginal) => {
  const actual = await importOriginal<typeof import('vue-i18n')>()
  return {
    ...actual,
    useI18n: () => ({
      t: (key: string, params?: Record<string, unknown>) => (params ? `${key} ${JSON.stringify(params)}` : key),
    }),
  }
})

import TaskNamingPage from './TaskNamingPage.vue'

/** 一份「像真的」的报告：每个数都挑成一眼能认出来的那种（1,234 / 25% / $1.23）。 */
function fixture(over: Record<string, unknown> = {}) {
  return {
    id: 'task-naming',
    title: '智能命名',
    summary: '话题标题的自动命名',
    days: 30,
    start: '2026-09-02',
    end: '2026-10-01',
    numbers: {
      calls: { value: 1234, failed: 12, success_rate: 0.9903 },
      tokens: { value: 456789, prompt: 300000, completion: 156789, cache_read: 0 },
      cost: {
        usd: 1.2345,
        source: 'gateway',
        budget_usd: 10.0,
        budget_duration: '30d',
        key_spend_usd: 1.25,
      },
      renames: { value: 96, name: 60, calibrate: 30, follow: 6 },
      person_edits: { value: 12 },
      overridden: { value: 12, named: 48, share: 0.25 },
    },
    trend: [
      { date: '2026-09-30', auto: 2, person: 0 },
      { date: '2026-10-01', auto: 1, person: 1 },
    ],
    ...over,
  }
}

function mountPage() {
  return render(TaskNamingPage, {
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

describe('智能命名的功能数据页', () => {
  it('窗口内的数按口径画出来，比例带着分母', async () => {
    report.mockResolvedValue(fixture())
    const { findByText, container } = mountPage()
    // 等的是**到货之后**才有的那个数，不是那行标题：标题在骨架那一帧就已经在页面上
    // 了（`findByText` 会立刻返回），报告还没落地就去读 textContent 会读到一排空骨架。
    await findByText('1,234')

    const text = container.textContent ?? ''
    // 调用数（千分位）与成功率（网关的口径，四舍五入到整数）。
    expect(text).toContain('1,234')
    expect(text).toContain('99%')
    expect(text).toContain('featureStats.naming.kpi.callsFailed {"failed":"12"}')
    expect(text).toContain('featureStats.naming.kpi.callsOf {"failed":"12","total":"1,234"}')
    // token 用量与拆分（问进去的 / 写回来的 / 缓存里读的）。
    expect(text).toContain('456,789')
    expect(text).toContain('featureStats.naming.kpi.tokenSplit {"prompt":"300,000","completion":"156,789","cache":"0"}')
    // 花费是网关自己记的账（不是估算）；额度那一行把它自己的花费和**周期**一起画 ——
    // 密钥记的花费跟的是网关的额度周期，不是这一页选的窗口。
    expect(text).toContain('$1.23')
    expect(text).toContain('featureStats.naming.kpi.budget {"spend":"$1.25","budget":"$10.00","duration":"30d"}')
    // 写过的标题：总数 + 三个阶段。
    expect(text).toContain('96')
    expect(text).toContain('featureStats.naming.kpi.stages {"name":"60","calibrate":"30","follow":"6"}')
    // 被人改掉：比例与**分母**（被自动命名过的房间数）都画出来。
    expect(text).toContain('25%')
    expect(text).toContain('featureStats.naming.kpi.overriddenOf {"value":"12","named":"48"}')
    // 统计窗口那行：两端的日期。
    expect(text).toContain('featureStats.page.window {"start":"2026-09-02","end":"2026-10-01"}')
  })

  it('两组分档都画出来：自动命名落在哪个阶段、人动了什么', async () => {
    report.mockResolvedValue(fixture())
    const { findByText, container } = mountPage()
    // 等的是**到货之后**才有的副行，不是那行标题：标题在骨架那一帧就已经在页面上
    // 了（`findByText` 会立刻返回），报告还没落地就去读 textContent 会读到 `n=0`。
    await findByText('featureStats.naming.stages.caption {"n":"96"}')

    const text = container.textContent ?? ''
    expect(text).toContain('featureStats.naming.stages.caption {"n":"96"}')
    expect(text).toContain('featureStats.naming.stages.name')
    expect(text).toContain('featureStats.naming.stages.calibrate')
    expect(text).toContain('featureStats.naming.stages.follow')
    expect(text).toContain('featureStats.naming.person.caption {"n":"12"}')
    expect(text).toContain('featureStats.naming.person.rename')
    // 折线两条：平台写的、人改的。
    expect(text).toContain('featureStats.naming.trend.auto')
    expect(text).toContain('featureStats.naming.trend.person')
  })

  it('读不到网关时是破折号加「读不到」，不是 $0', async () => {
    report.mockResolvedValue(
      fixture({
        numbers: {
          ...fixture().numbers,
          calls: { value: null, failed: null, success_rate: null },
          tokens: { value: null, prompt: null, completion: null, cache_read: null },
          cost: {
            usd: null,
            source: 'unavailable',
            budget_usd: null,
            budget_duration: null,
            key_spend_usd: null,
          },
        },
      })
    )
    const { findByText, container } = mountPage()
    await findByText('featureStats.naming.kpi.costUnavailable')

    const text = container.textContent ?? ''
    expect(text).not.toContain('$0')
    // 「为什么没有数」那句话在花费那张卡的口径注里（见 `mountPage` 的 stub）。
    expect(text).toContain('featureStats.naming.kpi.costUnknownNote')
    // 网关那四格是长破折号（不是 0），库里那两格照常有数。
    const nums = Array.from(container.querySelectorAll('.akpi__num')).map((el) => el.textContent?.trim())
    expect(nums.filter((num) => num === '—').length).toBe(4)
    expect(nums).toContain('96')
    expect(nums).toContain('25%')
    // 额度那一行没有值，整个不画 —— 不画成「$0 / $0」。
    expect(text).not.toContain('featureStats.naming.kpi.budget')
    // 库里那一半照常画：读不到网关不影响「写了多少、被改掉多少」。
    expect(text).toContain('featureStats.naming.kpi.overriddenOf {"value":"12","named":"48"}')
  })

  it('网关答了话、上面没有那把密钥时，说的是「没有这把密钥」，不是 $0', async () => {
    report.mockResolvedValue(
      fixture({
        numbers: {
          ...fixture().numbers,
          calls: { value: null, failed: null, success_rate: null },
          tokens: { value: null, prompt: null, completion: null, cache_read: null },
          cost: {
            usd: null,
            source: 'no-key',
            budget_usd: null,
            budget_duration: null,
            key_spend_usd: null,
          },
        },
      })
    )
    const { findByText, container } = mountPage()
    await findByText('featureStats.naming.kpi.costNoKey')

    const text = container.textContent ?? ''
    // 和「读不到网关」是两句话：这句说的不是「没读到」，是「上面没有这把密钥」。
    expect(text).toContain('featureStats.naming.kpi.costNoKeyNote')
    expect(text).not.toContain('featureStats.naming.kpi.costUnavailable')
    expect(text).not.toContain('featureStats.naming.kpi.costUnknownNote')
    expect(text).not.toContain('$0')
    // 网关那四格仍是长破折号，不是 0。
    const nums = Array.from(container.querySelectorAll('.akpi__num')).map((el) => el.textContent?.trim())
    expect(nums.filter((num) => num === '—').length).toBe(4)
    expect(text).toContain('featureStats.naming.kpi.overriddenOf {"value":"12","named":"48"}')
  })

  it('网关没报额度周期时，只画花费与额度，不替它编一个周期', async () => {
    report.mockResolvedValue(
      fixture({
        numbers: {
          ...fixture().numbers,
          cost: {
            usd: 1.2345,
            source: 'gateway',
            budget_usd: 10.0,
            budget_duration: null,
            key_spend_usd: 1.25,
          },
        },
      })
    )
    const { findByText, container } = mountPage()
    await findByText('$1.23')

    const text = container.textContent ?? ''
    // 不画成「周期 null」，也不退回去说「今日」——周期是网关说的，它没说就不说。
    expect(text).toContain('featureStats.naming.kpi.budgetNoPeriod {"spend":"$1.25","budget":"$10.00"}')
    expect(text).not.toContain('featureStats.naming.kpi.budget ')
    expect(text).not.toContain('null')
  })

  it('切窗口是重新取数（带 days），不是在本地筛', async () => {
    report.mockResolvedValue(fixture())
    const { findByText, getAllByRole } = mountPage()
    await findByText('featureStats.naming.trend.title')
    expect(report).toHaveBeenCalledWith(30)

    const seven = getAllByRole('tab').find((tab) => tab.textContent?.includes('featureStats.days.7'))
    expect(seven).toBeTruthy()
    await fireEvent.click(seven as HTMLElement)

    await waitFor(() => expect(report).toHaveBeenCalledWith(7))
  })

  it.each(['success', 'failure'])('旧窗口晚到的 %s 不覆盖已选窗口', async (outcome) => {
    let resolveOld!: (value: ReturnType<typeof fixture>) => void
    let rejectOld!: (reason: Error) => void
    report.mockReturnValueOnce(
      new Promise((resolve, reject) => {
        resolveOld = resolve
        rejectOld = reject
      })
    )
    report.mockResolvedValueOnce(fixture({ days: 7, start: '2026-09-25' }))
    const { getAllByRole, findByText, queryByText } = mountPage()
    const seven = getAllByRole('tab').find((tab) => tab.textContent?.includes('featureStats.days.7'))
    await fireEvent.click(seven as HTMLElement)
    const selectedWindow = 'featureStats.page.window {"start":"2026-09-25","end":"2026-10-01"}'
    await findByText(selectedWindow)
    if (outcome === 'success') resolveOld(fixture())
    else rejectOld(new Error('old request failed'))
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(queryByText(selectedWindow)).not.toBeNull()
    expect(queryByText('featureStats.page.loadFailed')).toBeNull()
  })

  it('读失败画错误态，不画一排 0', async () => {
    report.mockRejectedValueOnce(new Error('boom'))
    const { findByText, queryByText } = mountPage()

    expect(await findByText('featureStats.page.loadFailed')).toBeTruthy()
    // 一排 0 会被读成「这个月没花钱也没命名」，比一句「读不到」糟得多。
    expect(queryByText('featureStats.naming.trend.title')).toBeNull()
  })
})
