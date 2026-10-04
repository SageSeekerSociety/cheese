/**
 * 记忆的功能数据页（`/admin/feature-stats/memory`）。
 *
 * 这一页每一处要断言的都是「数字会说谎」的那一种读法：
 *
 * * **只出数，不出内容**：私人记忆按人聚合，报告和页面里都没有文件名、没有正文。
 * * **「没有索引」是破折号不是 0**：这一格画 0 读起来是「索引是空的」，所以那一格
 *   干脆是空的。
 * * **整理要能看出「挂着没回音」**：卡住那一档和完成 / 失败 / 被拦并列画。
 * * **窗口是服务端的问法**：切页签要**重新取数**（带 `days`），不是本地筛已到的行。
 * * **读失败不画成一片零**：拿不到报告时画错误态。
 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const report = vi.fn()
vi.mock('@/views/admin/features/featureApi', () => ({
  getMemoryReport: (...args: unknown[]) => report(...args),
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

import MemoryPage from './MemoryPage.vue'

/** 一份「像真的」的报告：每个数都挑成一眼能认出来的那种（999 / 6 / 250 行）。 */
function fixture(over: Record<string, unknown> = {}) {
  return {
    id: 'memory',
    title: '记忆',
    summary: '文件式记忆',
    days: 30,
    start: '2026-09-02',
    end: '2026-10-01',
    numbers: {
      projects: { value: 3 },
      entries: { value: 999 },
      personal: { value: 5, owners: 2 },
      index: { over: 1, over_lines: 1, over_bytes: 0 },
      hygiene: { orphan: 1, dangling: 2, over_body: 3 },
      reads: { value: 20 },
      dream: {
        last_completed_at: '2026-10-01T08:00:00',
        completed: 4,
        failed: 1,
        refused: 2,
        running: 1,
        stuck: 1,
      },
    },
    projects: [
      {
        project_id: 'aaaa1111',
        name: 'P A',
        entries: 8,
        personal: { entries: 5, owners: 2 },
        index: { lines: 250, bytes: 2048, over_lines: true, over_bytes: false },
        orphan: 1,
        dangling: 2,
        over_body: 3,
        reads: 20,
        dream: {
          last_completed_at: '2026-10-01T08:00:00',
          completed: 4,
          failed: 1,
          refused: 2,
          running: 1,
          stuck: 1,
          tokens: { value: 1, threshold: 2 },
        },
      },
      {
        project_id: 'bbbb2222',
        name: 'P B',
        entries: 0,
        personal: { entries: 0, owners: 0 },
        index: { lines: null, bytes: null, over_lines: false, over_bytes: false },
        orphan: 0,
        dangling: 0,
        over_body: 0,
        reads: 0,
        dream: {
          last_completed_at: null,
          completed: 0,
          failed: 0,
          refused: 0,
          running: 0,
          stuck: 0,
          tokens: { value: 0, threshold: 100 },
        },
      },
    ],
    trend: [
      { date: '2026-09-30', reads: 18 },
      { date: '2026-10-01', reads: 2 },
    ],
    ...over,
  }
}

function mountPage() {
  return render(MemoryPage, {
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

describe('记忆的功能数据页', () => {
  it('窗口内的数按口径画出来，比例和分母一起画', async () => {
    report.mockResolvedValue(fixture())
    const { findByText, container } = mountPage()
    // 等的是**到货之后**才有的那个数，不是那行标题：标题在骨架那一帧就已经在页面上
    // 了（`findByText` 会立刻返回）。
    await findByText('999')

    const text = container.textContent ?? ''
    expect(text).toContain('featureStats.memory.kpi.projects')
    expect(text).toContain('999')
    // 个人记忆：条数与涉及的人数一起画。
    expect(text).toContain('featureStats.memory.kpi.personalOf {"entries":"5","owners":"2"}')
    // 索引超预算：撑破的行数与字节数分开画（本项目 1 行超、0 个项目字节超）。
    expect(text).toContain('featureStats.memory.kpi.indexOf {"lines":"1","bytes":"0"}')
    // 待收拾：孤儿 1 + 悬空 2 + 超长 3 = 6，明细也画出来。
    expect(text).toContain('6')
    expect(text).toContain('featureStats.memory.kpi.hygieneOf {"orphan":"1","dangling":"2","over":"3"}')
    // 整理：被拦 2、卡住 1。
    expect(text).toContain('featureStats.memory.kpi.dreamOf {"refused":"2","stuck":"1"}')
    // 统计窗口那行：两端的日期。
    expect(text).toContain('featureStats.page.window {"start":"2026-09-02","end":"2026-10-01"}')
  })

  it('每个项目一行：索引超预算的带记号，没有索引的那格是空的', async () => {
    report.mockResolvedValue(fixture())
    const { findByText, container } = mountPage()
    await findByText('P A')

    const text = container.textContent ?? ''
    // 两个项目名都画出来（名字可能为空时退回 id 前 8 位，这里都有名字）。
    expect(text).toContain('P A')
    expect(text).toContain('P B')
    // 索引那一格：250 行 / 2K。
    expect(text).toContain('250 / 2K')
    // 卡住的项目在「最近整理」那一列写「卡住」。
    expect(text).toContain('featureStats.memory.table.stuck')
    // 待收拾那一格的明细收在 title 里。
    const titled = container.querySelector<HTMLElement>('td[title]')
    expect(titled?.getAttribute('title')).toBe(
      'featureStats.memory.table.pendingTitle {"orphan":"1","dangling":"2","over":"3"}'
    )
    // 没有索引的项目（P B）那一格是空的 —— 「没有索引」不画成 0。
    const rowB = [...container.querySelectorAll('tr')].find((tr) => tr.textContent?.includes('P B'))
    expect(rowB?.textContent).not.toMatch(/\b0\b/)
  })

  it('切窗口是重新取数（带新的 days），不在本地筛', async () => {
    report.mockResolvedValue(fixture())
    const { findByText, getAllByRole } = mountPage()
    await findByText('999')

    const tabs = getAllByRole('tab')
    const seven = tabs.find((tab) => tab.textContent?.includes('featureStats.days.7'))
    expect(seven).toBeTruthy()
    await fireEvent.click(seven!)

    await waitFor(() => expect(report).toHaveBeenCalledWith(7))
  })

  it('读失败画错误态，不画一排零', async () => {
    report.mockRejectedValue(new Error('boom'))
    const { findByText } = mountPage()
    await findByText('featureStats.page.loadFailed')
  })
})
