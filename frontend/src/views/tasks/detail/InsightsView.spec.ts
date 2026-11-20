import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import InsightsView from './InsightsView.vue'

import i18n, { setLocale } from '@/i18n'

const TASK_ID = 3

function task() {
  return {
    id: TASK_ID,
    name: '一道团队题',
    intro: '题',
    approved: 'APPROVED',
    participantLimit: 0,
    minTeamSize: 2,
    maxTeamSize: 3,
    deadline: null,
    createdAt: 1,
    creator: { id: 9, username: 'author', nickname: '出题人' },
    category: { id: 1, name: '默认分类' },
  }
}

/** 一条报名。`isTeam` / `team` 就是这条接口给的那两列。 */
function claim(over: Record<string, unknown>) {
  return {
    id: 1,
    member: { id: 1, name: '某人', intro: '', avatarId: null },
    createdAt: Date.now(),
    updatedAt: Date.now(),
    deadline: null,
    approved: 'APPROVED',
    isTeam: false,
    ...over,
  }
}

/** 画这一页签：名单与提交都是外面取好给的，读没读出来也由外面说。 */
function mount(
  participants: ReturnType<typeof claim>[],
  opts: { failed?: boolean; failureReason?: string | null; forbidden?: boolean; judgeStats?: unknown } = {}
) {
  return render(InsightsView, {
    props: {
      task: task() as never,
      roster: participants as never,
      reviewByParticipant: new Map(),
      canManage: true,
      loading: false,
      failed: opts.failed ?? false,
      failureReason: opts.failureReason ?? null,
      forbidden: opts.forbidden ?? false,
      judgeStats: (opts.judgeStats ?? null) as never,
    },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

/** 一块面板：按标题找（这一页的面板不止一块）。 */
function panel(title: string): Element | undefined {
  return Array.from(document.querySelectorAll('.panel')).find(
    (p) => p.querySelector('h3')?.textContent?.trim() === title
  )
}

/** 「小队构成」那一格的桶：标签 → 「N 人」。排序不看，桶名与人数才是判据。 */
function buckets(): Record<string, string> {
  const out: Record<string, string> = {}
  for (const row of Array.from(panel('小队构成')?.querySelectorAll('.bars__row') ?? [])) {
    const label = row.querySelector('.bars__label')?.textContent?.trim() ?? ''
    out[label] = row.querySelector('.bars__value')?.textContent?.trim() ?? ''
  }
  return out
}

describe('单题看板的「小队构成」', () => {
  // 桶名与单位按中文界面断言。
  beforeEach(() => setLocale('zh-CN'))
  afterEach(() => {
    cleanup()
  })

  it('两支不同的队伍各占一行', () => {
    mount([
      claim({
        id: 1,
        isTeam: true,
        member: { id: 101, name: '玄武队', intro: '', avatarId: null },
        team: { id: 101, name: '玄武队' },
      }),
      claim({
        id: 2,
        isTeam: true,
        member: { id: 102, name: '朱雀队', intro: '', avatarId: null },
        team: { id: 102, name: '朱雀队' },
      }),
      claim({ id: 3, member: { id: 201, name: '林小满', intro: '', avatarId: null } }),
    ])

    // 两队各一行 —— 按「是不是团队」分桶的话这里只有「小队 2 人」。
    expect(buckets()).toEqual({ 玄武队: '1 人', 朱雀队: '1 人', 单人: '1 人' })
  })

  it('是团队但拿不到队名时退回「小队」，不掉进「单人」', () => {
    mount([
      // 队已经不在（接口只给 isTeam，没有 team）＋ 一个按人领的。
      claim({ id: 1, isTeam: true, member: { id: 101, name: '', intro: '', avatarId: null } }),
      claim({ id: 2, member: { id: 201, name: '林小满', intro: '', avatarId: null } }),
    ])

    expect(buckets()).toEqual({ 小队: '1 人', 单人: '1 人' })
  })

  // 这一页是出题人拿来决策的：读失败留下的空名单画出来是「0 人领、0 份提交、通过率 0%」，
  // 和「这道题确实没人领」一模一样。失败要说出来，别装成一组真的数字。
  it('没读出来时画失败说明，不画一张全 0 的看板', async () => {
    const view = mount([], { failed: true, failureReason: '服务端打盹了' })

    expect(view.getByText('这道题的数据加载失败')).toBeTruthy()
    expect(view.getByText('服务端打盹了')).toBeTruthy()
    expect(view.queryByText('参与人数')).toBeNull()
    expect(panel('小队构成')).toBeUndefined()

    await fireEvent.click(view.getByText('重试'))
    expect(view.emitted('retry')).toHaveLength(1)
  })

  it('不给你看：说没权限，不摆重试', () => {
    const view = mount([], { failed: true, forbidden: true })

    expect(view.getByText('你没有权限查看')).toBeTruthy()
    expect(view.queryByText('重试')).toBeNull()
  })
})

describe('单题看板的「开始做后一直没动」', () => {
  beforeEach(() => setLocale('zh-CN'))
  afterEach(() => {
    cleanup()
  })

  const DAY = 86_400_000

  /** 那张数字卡上的数：按它的标签找。 */
  function stalledCount(view: ReturnType<typeof mount>): string | undefined {
    const label = view.getByText('开始做后一直没动')
    return label.closest('.metric')?.querySelector('.metric__value')?.textContent?.trim()
  }

  it('从开始做那一下算，不从申请那一下算；还没开始做的不算', () => {
    const appliedLongAgo = Date.now() - 30 * DAY
    const view = mount([
      // 一个月前申请、昨天才开始做：还没满 5 天。
      claim({ id: 1, createdAt: appliedLongAgo, startedAt: Date.now() - DAY }),
      // 一个月前申请、批了还没开始做：没有项目，谈不上没动。
      claim({ id: 2, createdAt: appliedLongAgo, startedAt: null }),
      // 6 天前开始做、一版没交：算。
      claim({ id: 3, createdAt: appliedLongAgo, startedAt: Date.now() - 6 * DAY }),
    ])

    expect(stalledCount(view)).toBe('1')
  })
})

/** `GET /tasks/{id}/judge/stats` 的一份回答：两个阶段挂着测试点，看的是第二个。 */
function judgeStats(over: Record<string, unknown> = {}) {
  return {
    stages: ['s1', 's2'],
    stage: 's2',
    stats: {
      kind: 'IO',
      submissions: 30,
      judged: 25,
      scoreFailed: 1,
      failed: 2,
      inFlight: 0,
      notJudged: 2,
      outdated: 0,
      averageRate: 0.62,
      buckets: [3, 0, 0, 0, 0, 2, 4, 0, 6, 10],
      cases: [
        {
          name: '大数据',
          visibility: 'HIDDEN',
          judged: 25,
          passed: 7,
          rate: 0.28,
          topFailure: { status: 'timeout', count: 18 },
        },
        { name: '样例', visibility: 'SAMPLE', judged: 25, passed: 25, rate: 1, topFailure: null },
      ],
    },
    credits: { total: 12.5, submissions: 10, selfTests: 2.5, machineMs: 600_000, chargedRuns: 20 },
    ...over,
  }
}

describe('单题看板的「测试点」', () => {
  beforeEach(() => setLocale('zh-CN'))
  afterEach(() => {
    cleanup()
  })

  it('最差的用例在前，说出没过的人最多是哪种状态', () => {
    const view = mount([], { judgeStats: judgeStats() })

    const rows = Array.from(document.querySelectorAll('[data-testid="insights-judge-cases"] li'))
    expect(rows.map((r) => r.querySelector('.tji__name')?.textContent?.trim())).toEqual(['大数据', '样例'])
    expect(rows[0].textContent).toContain('7 / 25 人通过')
    expect(rows[0].textContent).toContain('28%')
    expect(rows[0].textContent).toContain('18 人超时')
    expect(rows[1].textContent).toContain('全部通过')
    expect(view.getByText('62%')).toBeTruthy()
  })

  it('测试分十档各几人，不在图里的几份另说', () => {
    mount([], { judgeStats: judgeStats() })

    const spread = document.querySelector('[data-testid="insights-judge-spread"]')
    const counts = Array.from(spread?.querySelectorAll('.tji__col-n') ?? []).map((n) => n.textContent?.trim())
    expect(counts).toEqual(['3', '', '', '', '', '2', '4', '', '6', '10'])
    expect(document.body.textContent).toContain('判分失败 1 份、评测失败 2 份、评测中 0 份、没有评测 2 份')
  })

  it('写出评测累计花的额度，分提交评测与自测', () => {
    const view = mount([], { judgeStats: judgeStats() })

    const card = view.getByText('评测累计花的额度').closest('.metric')
    expect(card?.querySelector('.metric__value')?.textContent?.trim()).toBe('12.50')
    expect(card?.textContent).toContain('提交评测 10 · 学生自测 2.50 · 机器 10 分钟')
  })

  it('挂着几个阶段时能切阶段', async () => {
    const view = mount([], { judgeStats: judgeStats() })

    await fireEvent.click(view.getByText('阶段 1'))
    expect(view.emitted('judgeStage')).toEqual([['s1']])
  })

  it('没有测试点的题不画这一块', () => {
    mount([], { judgeStats: judgeStats({ stages: [], stage: null, stats: null }) })

    expect(document.querySelector('[data-testid="insights-judge"]')).toBeNull()
  })
})
