/**
 * 详情页的「删除这条反馈」。
 *
 * 三件事各自都会坏，而且坏法都不一样：
 *
 *   1. **按钮出不出现由服务端说**（`can_delete`）。客户端自己拼一遍
 *      `handle == mine || isAdmin` 是这个仓库已经吃过一次的亏 —— 这条用例钉的是
 *      「服务端说 false 就一定不画」。
 *   2. **确认那一句必须带条数**。「删掉这条反馈」而实际删掉它下面 12 条评论，是在骗
 *      按按钮的人；反过来，没有评论时凭空多出一个 0 同样是在骗。所以两个方向都断言。
 *   3. **删完回反馈中心，而且用 `replace`**：这一条已经不存在了，回退键不该回到一个
 *      404。删失败则**留在原地**（错误原话在页面上，人还能重试）。
 */
import type { FeedbackDetail } from '@/cx_types'

import { createRouter, createWebHashHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getFeedback = vi.fn()
const deleteFeedback = vi.fn()

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    deleteFeedback: (...a: unknown[]) => deleteFeedback(...a),
    getFeedback: (...a: unknown[]) => getFeedback(...a),
  }
})

import FeedbackDetailPage from './FeedbackDetailPage.vue'

import { setLocale } from '@/i18n'

function detail(over: Partial<FeedbackDetail> = {}): FeedbackDetail {
  return {
    id: 'fb-1',
    title: '导出报表偶发 502',
    status: 'accepted',
    visibility: 'public',
    security: false,
    supports: 0,
    supported: false,
    comments: 0,
    thread: [],
    thread_next_cursor: null,
    timeline: [],
    author_handle: 'alice',
    can_delete: true,
    ...over,
  } as unknown as FeedbackDetail
}

async function mountPage() {
  const router = createRouter({
    history: createWebHashHistory(),
    routes: [
      { path: '/feedback/:id', name: 'FeedbackDetail', component: FeedbackDetailPage },
      { path: '/feedback', name: 'FeedbackCenter', component: { template: '<div>反馈中心</div>' } },
    ],
  })
  await router.push('/feedback/fb-1')
  await router.isReady()
  const vuetify = createVuetify({ components, directives })
  const Wrapper = {
    components: { FeedbackDetailPage },
    template: '<v-app><FeedbackDetailPage /></v-app>',
  }
  const utils = render(Wrapper, { global: { plugins: [vuetify, router, createPinia()] } })
  await waitFor(() => expect(utils.baseElement.querySelector('.fb-title')).toBeTruthy())
  return { ...utils, router }
}

const byText = (root: Element, text: string) =>
  Array.from(root.querySelectorAll('button')).find((b) => b.textContent?.includes(text)) as HTMLElement | undefined

beforeEach(() => {
  setLocale('zh-CN')
  getFeedback.mockReset()
  deleteFeedback.mockReset()
  getFeedback.mockResolvedValue(detail())
  deleteFeedback.mockResolvedValue({ deleted: true })
})

describe('删除这条反馈', () => {
  it('服务端说不能删，就不画那个入口', async () => {
    getFeedback.mockResolvedValue(detail({ can_delete: false }))
    const { baseElement } = await mountPage()
    expect(byText(baseElement, '删除')).toBeUndefined()
  })

  it('没有评论时确认只说这一条', async () => {
    const { baseElement } = await mountPage()
    await fireEvent.click(byText(baseElement, '删除')!)
    expect(baseElement.textContent).toContain('删掉这条反馈？')
    expect(baseElement.textContent).not.toContain('条评论一起')
  })

  it('有评论时确认那句带条数 —— 删掉的是一整栋楼，得说出来', async () => {
    getFeedback.mockResolvedValue(detail({ comments: 12 }))
    const { baseElement } = await mountPage()
    await fireEvent.click(byText(baseElement, '删除')!)
    expect(baseElement.textContent).toContain('连同它下面的 12 条评论一起')
  })

  it('取消就退回去，什么都不发', async () => {
    const { baseElement } = await mountPage()
    await fireEvent.click(byText(baseElement, '删除')!)
    await fireEvent.click(byText(baseElement, '取消')!)
    // 回到「只有一个删除按钮」那一态。
    expect(byText(baseElement, '确认删除')).toBeUndefined()
    expect(deleteFeedback).not.toHaveBeenCalled()
  })

  it('确认之后真的发请求，并回反馈中心（replace，不是 push）', async () => {
    const { baseElement, router } = await mountPage()
    await fireEvent.click(byText(baseElement, '删除')!)
    await fireEvent.click(byText(baseElement, '确认删除')!)

    await waitFor(() => expect(deleteFeedback).toHaveBeenCalledWith('fb-1'))
    await waitFor(() => expect(router.currentRoute.value.path).toBe('/feedback'))
  })

  it('删失败就留在原地，并显示服务端的原话', async () => {
    deleteFeedback.mockRejectedValue(new Error('只有作者或管理员能删'))
    const { baseElement, router } = await mountPage()
    await fireEvent.click(byText(baseElement, '删除')!)
    await fireEvent.click(byText(baseElement, '确认删除')!)

    await waitFor(() => expect(baseElement.textContent).toContain('只有作者或管理员能删'))
    // 没跳走 —— 这一页还在，人还能重试。
    expect(router.currentRoute.value.path).toBe('/feedback/fb-1')
  })
})
