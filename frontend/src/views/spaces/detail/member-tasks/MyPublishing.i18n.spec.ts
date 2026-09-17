// 「我发布的」这一屏：概览卡、筛选卡（含两个切换 chip）、空态。
//
// 列表卡片 MyPublishedTaskCard 不在这一切片里、自己还欠着中文，所以接口回空列表——
// 它根本不渲染——整页扫汉字扫到的就只剩这一屏的字。卡片里那些状态词走 helpers.ts，
// 那支单独有 helpers.i18n.spec.ts。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  getMyPublishingOverview: vi.fn(),
  getMyPublishedTasks: vi.fn(),
  fetchCategories: vi.fn(),
  error: vi.fn(),
  success: vi.fn(),
  replace: vi.fn(),
  push: vi.fn(),
}))
vi.mock('@/network/api/spaces', () => ({ SpacesApi: mocks }))
vi.mock('vuetify-sonner', () => ({ toast: { success: mocks.success, error: mocks.error } }))
// 空间 store 整块换掉：这一屏只从它取 currentSpaceId / currentSpace / categories。
vi.mock('@/stores/space', async () => {
  const { ref } = await import('vue')
  return {
    useSpaceStore: () => ({
      currentSpaceId: ref(1),
      currentSpace: ref(null),
      categories: ref([]),
      fetchCategories: mocks.fetchCategories,
    }),
  }
})
// 不能整块替掉 vue-router：间接引到的 @/router 还要 createRouter。
vi.mock('vue-router', async (importOriginal) => ({
  ...(await importOriginal<typeof import('vue-router')>()),
  useRoute: () => ({ params: { spaceId: '1' }, query: {} }),
  useRouter: () => ({ replace: mocks.replace, push: mocks.push }),
}))

import MyPublishing from './MyPublishing.vue'

import i18n, { setLocale } from '@/i18n'

const CJK = /[㐀-䶿一-鿿豈-﫿]/

/** 这个文件里没有靠拼接拼成的一句话，折空白只为断言好写。 */
const text = (element: Element) => (element.textContent ?? '').replace(/\s+/g, ' ')

const overview = {
  spaceId: 1,
  taskCount: 2,
  approvedTaskCount: 1,
  pendingTaskApprovalCount: 1,
  disapprovedTaskCount: 0,
  participantCount: 3,
  approvedParticipantCount: 2,
  pendingParticipantApprovalCount: 1,
  submittedParticipantCount: 1,
  pendingReviewCount: 1,
  successfulParticipantCount: 1,
}

function mountPage(fail = false) {
  mocks.fetchCategories.mockResolvedValue(undefined)
  if (fail) {
    mocks.getMyPublishingOverview.mockRejectedValue(new Error('boom'))
    mocks.getMyPublishedTasks.mockRejectedValue(new Error('boom'))
  } else {
    mocks.getMyPublishingOverview.mockResolvedValue({ data: overview })
    mocks.getMyPublishedTasks.mockResolvedValue({ data: { tasks: [] } })
  }
  return render(MyPublishing, {
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
  const { page } = await mountAndSettle('还没有发布记录')
  const rendered = page()

  expect(rendered).toContain('我发布的赛题')
  expect(rendered).toContain('发布赛题')

  // 四张概览卡的标题与说明
  expect(rendered).toContain('待审核题目')
  expect(rendered).toContain('还在等待空间管理员审核的题目数')
  expect(rendered).toContain('待审核报名')
  expect(rendered).toContain('需要你尽快确认的报名主体')
  expect(rendered).toContain('待评审提交')
  expect(rendered).toContain('已经提交但还没有完成评审的作品')
  expect(rendered).toContain('成功主体数')
  expect(rendered).toContain('当前空间内通过你题目的成功主体')

  // 筛选卡：标题、清空按钮、四个下拉的标签
  expect(rendered).toContain('筛选')
  expect(rendered).toContain('清空筛选')
  expect(rendered).toContain('分类')
  expect(rendered).toContain('题目审批状态')
  expect(rendered).toContain('排序字段')
  expect(rendered).toContain('排序方向')

  // 下拉上画着当前选中项的标题：全部分类 / 全部 / 最新发布 / 降序
  expect(rendered).toContain('全部分类')
  expect(rendered).toContain('最新发布')
  expect(rendered).toContain('降序')

  // 两个筛选 chip 跟概览卡用的是同一批词
  expect(rendered).toContain('待审核报名')
  expect(rendered).toContain('待评审提交')

  expect(rendered).toContain('你在这个空间下还没有发布过赛题，发布后会在这里集中查看审核和参与状态。')
  expect(rendered).toContain('去发布赛题')
})

it('讲英文：整屏一个汉字都不剩', async () => {
  setLocale('en')
  const { page } = await mountAndSettle('Nothing published yet')
  const rendered = page()

  expect(rendered).toContain('Challenges I published')
  expect(rendered).toContain('Publish challenge')

  expect(rendered).toContain('Challenges awaiting approval')
  expect(rendered).toContain('Challenges still waiting for a space admin to review them')
  expect(rendered).toContain('Participants awaiting approval')
  expect(rendered).toContain('Participants waiting for your confirmation')
  expect(rendered).toContain('Submissions awaiting review')
  expect(rendered).toContain('Work that is submitted but not yet reviewed')
  expect(rendered).toContain('Successful participants')
  expect(rendered).toContain('Participants who passed your challenges in this space')

  expect(rendered).toContain('Filter')
  expect(rendered).toContain('Clear filters')
  expect(rendered).toContain('Category')
  expect(rendered).toContain('Challenge approval')
  expect(rendered).toContain('Sort by')
  expect(rendered).toContain('Sort order')

  expect(rendered).toContain('All categories')
  expect(rendered).toContain('Recently published')
  expect(rendered).toContain('Descending')

  expect(rendered).toContain("You haven't published a challenge in this space yet.")
  expect(rendered).toContain('Publish a challenge')
  expect(CJK.test(rendered), rendered).toBe(false)
})

it('切成英文后，已经画出来的字立刻跟着换', async () => {
  const { page } = await mountAndSettle('还没有发布记录')
  expect(page()).toContain('我发布的赛题')
  expect(page()).toContain('待评审提交')

  setLocale('en')
  await vi.waitFor(() => expect(page(), '标题').toContain('Challenges I published'))
  const switched = page()
  expect(switched).toContain('Filter')
  expect(switched).toContain('Challenges awaiting approval')
  expect(switched).toContain('Submissions awaiting review')
  // 下拉里画着的那一项也要跟着换：它来自 computed，不是挂载时算一次的常量。
  expect(switched).toContain('All categories')
  expect(switched).toContain('Recently published')
  expect(switched).not.toContain('全部分类')
  expect(switched).not.toContain('最新发布')
  expect(switched).toContain('Nothing published yet')
  expect(CJK.test(switched), switched).toBe(false)
})

it('接口挂了的时候，两个提示也是当前语言', async () => {
  mountPage(true)
  await vi.waitFor(() => expect(mocks.error).toHaveBeenCalledTimes(2))
  expect(mocks.error).toHaveBeenCalledWith('加载发布概览失败')
  expect(mocks.error).toHaveBeenCalledWith('加载我发布的赛题失败')

  setLocale('en')
  mocks.error.mockClear()
  mountPage(true)
  await vi.waitFor(() => expect(mocks.error).toHaveBeenCalledTimes(2))
  expect(mocks.error).toHaveBeenCalledWith("Couldn't load your publishing overview")
  expect(mocks.error).toHaveBeenCalledWith("Couldn't load the challenges you published")
})
