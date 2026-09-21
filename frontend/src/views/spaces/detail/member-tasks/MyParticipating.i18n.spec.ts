// 「我参与的」这一屏：概览卡、筛选卡、空态都是这一屏自己的文案。
//
// 两张列表卡片（MyParticipationCard / MyPublishedTaskCard）不在这一切片里，自己还欠着中文，
// 所以这个用例让接口回空列表——卡片根本不渲染——整页扫汉字扫到的就只剩这一屏的字。
// 卡片里那些状态词走的是 helpers.ts，那支单独有 helpers.i18n.spec.ts。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  getMyParticipatingOverview: vi.fn(),
  getMyParticipations: vi.fn(),
  error: vi.fn(),
  success: vi.fn(),
  replace: vi.fn(),
  push: vi.fn(),
}))
vi.mock('@/network/api/spaces', () => ({ SpacesApi: mocks }))
vi.mock('vuetify-sonner', () => ({ toast: { success: mocks.success, error: mocks.error } }))
// 不能整块替掉 vue-router：间接引到的 @/router 还要 createRouter。
vi.mock('vue-router', async (importOriginal) => ({
  ...(await importOriginal<typeof import('vue-router')>()),
  useRoute: () => ({ params: { spaceId: '1' }, query: {} }),
  useRouter: () => ({ replace: mocks.replace, push: mocks.push }),
}))

import MyParticipating from './MyParticipating.vue'

import i18n, { setLocale } from '@/i18n'

const CJK = /[㐀-䶿一-鿿豈-﫿]/

/** 这个文件里没有靠拼接拼成的一句话，折空白只为断言好写。 */
const text = (element: Element) => (element.textContent ?? '').replace(/\s+/g, ' ')

const overview = {
  spaceId: 1,
  participationCount: 4,
  approvedParticipationCount: 3,
  pendingApprovalCount: 1,
  awaitingSubmissionCount: 1,
  pendingReviewCount: 1,
  resubmittableCount: 1,
  successfulCount: 0,
  failedCount: 0,
}

/** 列表固定回空：见文件头——卡片不在这一切片里。 */
function mountPage(fail = false) {
  if (fail) {
    mocks.getMyParticipatingOverview.mockRejectedValue(new Error('boom'))
    mocks.getMyParticipations.mockRejectedValue(new Error('boom'))
  } else {
    mocks.getMyParticipatingOverview.mockResolvedValue({ data: overview })
    mocks.getMyParticipations.mockResolvedValue({ data: { participations: [] } })
  }
  return render(MyParticipating, {
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

function mountAndSettle(marker: string) {
  const view = mountPage()
  return vi
    .waitFor(() => expect(text(view.baseElement), marker).toContain(marker))
    .then(() => ({ view, page: () => text(view.baseElement) }))
}

beforeEach(() => {
  setLocale('zh-CN')
  vi.clearAllMocks()
  // happy-dom 不给 visualViewport，而 Vuetify 的下拉定位会真的去读它。
  vi.stubGlobal('visualViewport', new EventTarget())
  vi.stubGlobal('devicePixelRatio', 1)
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it('讲中文：概览、筛选、空态都是中文', async () => {
  const { page } = await mountAndSettle('还没有参与记录')
  const rendered = page()

  expect(rendered).toContain('我参与的赛题')
  expect(rendered).toContain('去看全部赛题')

  // 四张概览卡的标题与说明
  expect(rendered).toContain('待审核')
  expect(rendered).toContain('报名还在等待老师或管理员确认')
  expect(rendered).toContain('待提交')
  expect(rendered).toContain('已经通过审核，但还没有完成提交')
  expect(rendered).toContain('待评审')
  expect(rendered).toContain('作品已经提交，等待老师给出评审结果')
  expect(rendered).toContain('可重提')
  expect(rendered).toContain('被打回但仍然可以继续修改并重新提交')

  // 筛选卡：标题、清空按钮、五个下拉的标签
  expect(rendered).toContain('筛选')
  expect(rendered).toContain('清空筛选')
  expect(rendered).toContain('报名审批状态')
  expect(rendered).toContain('完成状态')
  expect(rendered).toContain('参与身份')
  expect(rendered).toContain('排序字段')
  expect(rendered).toContain('排序方向')

  // 下拉上画着当前选中项的标题：全部 / 最近加入 / 降序
  expect(rendered).toContain('最近加入')
  expect(rendered).toContain('降序')

  expect(rendered).toContain('你在这个空间里还没有参与任何赛题，可以先去全部赛题里挑一个开始。')
})

it('讲英文：整屏一个汉字都不剩', async () => {
  setLocale('en')
  const { page } = await mountAndSettle('Nothing joined yet')
  const rendered = page()

  expect(rendered).toContain('Challenges I joined')
  expect(rendered).toContain('View all challenges')

  expect(rendered).toContain('Pending review')
  expect(rendered).toContain('Your registration is waiting for a teacher or admin to confirm it')
  expect(rendered).toContain('Not submitted')
  expect(rendered).toContain("Your registration is approved, but you haven't submitted yet")
  expect(rendered).toContain('Awaiting review')
  expect(rendered).toContain("Your work is submitted and waiting for a teacher's review")
  expect(rendered).toContain('Can resubmit')
  expect(rendered).toContain('Sent back, but you can still revise and resubmit it')

  expect(rendered).toContain('Filter')
  expect(rendered).toContain('Clear filters')
  expect(rendered).toContain('Registration approval')
  expect(rendered).toContain('Completion status')
  expect(rendered).toContain('Participation type')
  expect(rendered).toContain('Sort by')
  expect(rendered).toContain('Sort order')

  expect(rendered).toContain('Recently joined')
  expect(rendered).toContain('Descending')

  expect(rendered).toContain("You haven't joined a challenge in this space yet.")
  expect(CJK.test(rendered), rendered).toBe(false)
})

it('切成英文后，已经画出来的字立刻跟着换', async () => {
  const { page } = await mountAndSettle('还没有参与记录')
  expect(page()).toContain('我参与的赛题')
  expect(page()).toContain('筛选')

  setLocale('en')
  await vi.waitFor(() => expect(page(), '标题').toContain('Challenges I joined'))
  const switched = page()
  expect(switched).toContain('Filter')
  expect(switched).toContain('Clear filters')
  // 下拉里画着的那一项也要跟着换：它来自 computed，不是挂载时算一次的常量。
  expect(switched).toContain('Recently joined')
  expect(switched).not.toContain('最近加入')
  expect(switched).toContain('Nothing joined yet')
  expect(CJK.test(switched), switched).toBe(false)
})

it('接口挂了的时候，两个提示也是当前语言', async () => {
  mountPage(true)
  await vi.waitFor(() => expect(mocks.error).toHaveBeenCalledTimes(2))
  expect(mocks.error).toHaveBeenCalledWith('加载参与概览失败')
  expect(mocks.error).toHaveBeenCalledWith('加载我参与的赛题失败')

  setLocale('en')
  mocks.error.mockClear()
  mountPage(true)
  await vi.waitFor(() => expect(mocks.error).toHaveBeenCalledTimes(2))
  expect(mocks.error).toHaveBeenCalledWith("Couldn't load your participation overview")
  expect(mocks.error).toHaveBeenCalledWith("Couldn't load the challenges you joined")
})
