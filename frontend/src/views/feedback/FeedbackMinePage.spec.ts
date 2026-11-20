/**
 * 「我的反馈」这一页有没有真的接上。
 *
 * 五件事各自钉一条，都是「不钉就会悄悄坏、而且坏得像没坏」的那类：
 *
 *   1. **挂载就拉**。`/feedback/mine` 的接口和 api.ts 的 `listMyFeedback` 上一轮
 *      就在了，只是没有任何页面调它 —— 这一页存在的理由就是那一下调用。
 *   2. **失败不冒充空**。拉挂了画「你还没有提过反馈」是把一次网络故障说成
 *      「平台没有你的数据」，这正是反馈中心那条错误用例修过的同一类 bug。
 *   3. **支持按钮在这一页也认账**。store 的 `_find` / `_patch` 要覆盖 `mineItems`：
 *      漏掉的表现是按钮按得下去、有按下效果、数字一动不动、控制台一句话也没有。
 *   4. **未读游标在这一页推进**。这一页列出的正是未读计数统计的那批，所以「打开这一页」
 *      就是「看过它们了」；漏掉的表现是顶栏那个未读点看完清单还在。
 *   5. **空态那颗按钮去哪由状态定**、**翻页接着往后取** —— 都是容器把事件接对才成立，
 *      接错了屏幕上什么都不动。
 */
import type { FeedbackCard } from '@/cx_types'

import { createRouter, createWebHashHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const listMyFeedback = vi.fn()
const supportFeedback = vi.fn()
const getFeedbackCounts = vi.fn()
const markFeedbackRead = vi.fn()

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    getFeedbackCounts: (...a: unknown[]) => getFeedbackCounts(...a),
    listMyFeedback: (...a: unknown[]) => listMyFeedback(...a),
    markFeedbackRead: (...a: unknown[]) => markFeedbackRead(...a),
    supportFeedback: (...a: unknown[]) => supportFeedback(...a),
  }
})

import FeedbackMinePage from './FeedbackMinePage.vue'

import { setLocale } from '@/i18n'
import FeedbackRoutes from '@/router/feedback'
import { resetFeedbackCaches, useFeedbackStore } from '@/stores/feedback'

// `author_handle` 得给：卡片会把它交给头像组件，缺了 Vue 会在控制台喊一句 prop
// 类型不对的警告。其余字段靠 `as unknown as` 补。
const CARD = {
  id: 'fb-1',
  title: '导出报表偶发 502',
  supports: 3,
  comments: 0,
  author_handle: 'alice',
  status: 'accepted',
} as unknown as FeedbackCard

function mountPage(pinia: ReturnType<typeof createPinia> = createPinia()) {
  const router = createRouter({
    history: createWebHashHistory(),
    // 用**应用真的那张表**：卡片里是一条 `<router-link :to="{ name: 'FeedbackDetail' }">`，
    // 名字解析不到时 Vue Router 是在**渲染那一刻**抛「No match for ...」的（不是导航
    // 时抛），而这条用例根本不点那条链接。手抄一条同名路由也能过，但路由改名时它不会
    // 跟着改 —— 抄一份就是给「用例和路由表悄悄分家」留一个后门。
    routes: [...FeedbackRoutes, { path: '/:pathMatch(.*)*', component: { template: '<div />' } }],
  })
  const vuetify = createVuetify({ components, directives })
  // 套一层 `v-app`：Vuetify 的主题变量与排版挂在它渲染出来的 `.v-application` 上，
  // 少了这一层，组件还在、主题却不在（底色、字号都不对）。
  const Wrapper = {
    components: { FeedbackMinePage },
    template: '<v-app><FeedbackMinePage /></v-app>',
  }
  return { ...render(Wrapper, { global: { plugins: [vuetify, router, pinia] } }), router }
}

beforeEach(() => {
  // 这一份钉的是**中文那一份文案**（用例里比的就是那几句）。文案搬进 i18n 之后，
  // 测试环境的默认语言是英文，不钉语言这里量到的就是另一句话。
  setLocale('zh-CN')
  // 「我的反馈」的缓存是模块级的（store 里 `mineCache`）：不清的话，后一个用例会先画
  // 前一个用例留下的那一页。
  resetFeedbackCaches()
  listMyFeedback.mockReset()
  supportFeedback.mockReset()
  getFeedbackCounts.mockReset()
  markFeedbackRead.mockReset()
  markFeedbackRead.mockResolvedValue(undefined)
  getFeedbackCounts.mockResolvedValue({ all: 1, hot: 0, resolved: 0, unread: 0 })
})

describe('我的反馈', () => {
  it('挂载就拉那一页，并把这一页的 `counts` 之外的列表画出来', async () => {
    listMyFeedback.mockResolvedValue({ data: [CARD], total: 1, counts: {} })

    const { findByText } = mountPage()

    expect(await findByText('导出报表偶发 502')).toBeTruthy()
    // 一页拉完：这一版没有翻页 UI，20 条默认值会让清单看起来「就只有这些」。
    expect(listMyFeedback).toHaveBeenCalledWith({ pageSize: 50 })
    expect(await findByText('共 1 条。')).toBeTruthy()
  })

  it('拉失败画的是那句话，不冒充「你还没有提过反馈」', async () => {
    listMyFeedback.mockRejectedValue(new Error('「我的反馈」加载失败'))

    const { queryByText, baseElement } = mountPage()

    // 钉页面里**这一处**（`.bes`，共用的那块空态），不是「树上某处有这句话」：要验的
    // 正是失败画在空态里，而不是被谁吸收了。
    await waitFor(() => {
      expect(baseElement.querySelector('.fb-page__inner .bes')?.textContent).toContain('「我的反馈」加载失败')
    })
    expect(queryByText('你还没有提过反馈，也没有指派给你的')).toBeNull()
  })

  it('没有内容时才说「还没有」，并且就地给一条提交的路', async () => {
    listMyFeedback.mockResolvedValue({ data: [], total: 0, counts: {} })

    const { findByText } = mountPage()

    expect(await findByText('你还没有提过反馈，也没有指派给你的')).toBeTruthy()
    expect(await findByText('提交一条')).toBeTruthy()
  })

  it('在这一页点支持，计数跟着服务端回的数走', async () => {
    listMyFeedback.mockResolvedValue({ data: [CARD], total: 1, counts: {} })
    // 服务端回的是**写完之后**的计数，不是增量：客户端 +1 会在两个同时点的人那里
    // 渲染出一个从来没存在过的数字。
    supportFeedback.mockResolvedValue({ count: 4, supported: true })

    const { findByText, getByRole, baseElement } = mountPage()
    await findByText('导出报表偶发 502')

    // 可访问名字里带着当前的人数（`支持这个反馈，当前 3 人支持`）：卡片上只剩图标和
    // 数字，读屏得从名字里听出「这是什么、现在几个人」。所以按前缀匹配。
    getByRole('button', { name: /^支持这个反馈/ }).click()

    await waitFor(() => {
      expect(supportFeedback).toHaveBeenCalledWith('fb-1')
    })
    await waitFor(() => {
      expect(baseElement.querySelector('.fb-card__count')?.textContent).toBe('4')
    })
  })

  it('还有未读时，打开这一页就把已读游标推进一次', async () => {
    listMyFeedback.mockResolvedValue({ data: [CARD], total: 1, counts: {} })

    // 未读数是应用外壳在别处问服务端拿到的；这里把它摆成「有 5 条没看」。容器里那句
    // 判据读的正是它。
    const pinia = createPinia()
    setActivePinia(pinia)
    useFeedbackStore().counts = {
      all: 1,
      hot: 0,
      resolved: 0,
      deployed: 0,
      unread: 5,
    }

    const { findByText } = mountPage(pinia)
    await findByText('导出报表偶发 502')

    // 清单列出的正是未读统计的那批，所以「打开这一页」=「看过它们了」：游标得推一次。
    await waitFor(() => expect(markFeedbackRead).toHaveBeenCalledTimes(1))
  })

  it('没有未读就不推游标：省下一趟没人受益的请求', async () => {
    listMyFeedback.mockResolvedValue({ data: [CARD], total: 1, counts: {} })

    // counts 默认 unread=0（没有那 5 条）
    const { findByText } = mountPage()
    await findByText('导出报表偶发 502')

    expect(markFeedbackRead).not.toHaveBeenCalled()
  })

  it('空态那颗「提交一条」带人去提交页，不是留在原地', async () => {
    listMyFeedback.mockResolvedValue({ data: [], total: 0, counts: {} })

    const { findByText, router } = mountPage()
    // 路由得先「就绪」，否则组件里那一下 `push` 会被还在飞的首次导航顶掉。
    await router.isReady()
    const submit = await findByText('提交一条')

    await fireEvent.click(submit)

    // 接错的表现：按钮画得出来、点下去没动静（导航那一步没接到路由上）。
    await waitFor(() => expect(router.currentRoute.value.name).toBe('FeedbackSubmit'))
  })

  it('拉挂了之后那「重试」真的再问一次服务端', async () => {
    listMyFeedback.mockRejectedValueOnce(new Error('「我的反馈」加载失败'))

    const { findByText } = mountPage()
    const retry = await findByText('重试')

    const before = listMyFeedback.mock.calls.length
    listMyFeedback.mockResolvedValue({ data: [CARD], total: 1, counts: {} })
    await fireEvent.click(retry)

    await waitFor(() => expect(listMyFeedback.mock.calls.length).toBeGreaterThan(before))
    expect(await findByText('导出报表偶发 502')).toBeTruthy()
  })

  it('「加载更多」接着往后取，带的是已经取回来过的行数', async () => {
    listMyFeedback.mockResolvedValueOnce({ data: [CARD], total: 60, counts: {} }).mockResolvedValueOnce({
      data: [{ ...CARD, id: 'fb-2', title: '第二条' } as unknown as FeedbackCard],
      total: 60,
      counts: {},
    })

    const { findByText } = mountPage()
    await findByText('导出报表偶发 502')

    const more = await findByText('加载更多')
    await fireEvent.click(more)

    // 偏移量是**取回来过的行数**（这一页 1 条），不是 items.length 的别名 —— 这一下接错
    // 就是「第二页从第 0 行开始」，两份清单叠着画，而两份各自看着都对。
    await waitFor(() => {
      expect(listMyFeedback).toHaveBeenCalledWith({ pageSize: 50, pageStart: 1 })
    })
    expect(await findByText('第二条')).toBeTruthy()
  })
})
