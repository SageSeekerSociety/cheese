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
  opts: { failed?: boolean; failureReason?: string | null; forbidden?: boolean } = {}
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

    expect(view.getByText('这道题的数据没读出来')).toBeTruthy()
    expect(view.getByText('服务端打盹了')).toBeTruthy()
    expect(view.queryByText('领取人数')).toBeNull()
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
