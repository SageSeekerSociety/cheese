/**
 * 详情页底部那个评论框：什么时候展开、什么时候收回去、焦点去哪儿。
 *
 * 它现在是 **`position: sticky` 挂在评论区底部**的（理由写在 `.fb-composer` 那条注释
 * 里），所以收起态不是图省事：常驻三行框加按钮差不多 130px，那是屏幕上永久少掉的
 * 一屏。sticky 本身是布局，jsdom 量不到 —— 仓库对 CSS 的老规矩是够不着的部分看真
 * 浏览器（`scroll.spec.ts` 开头解释了为什么）。这里钉的是**状态机**那一半。
 *
 * 里面只有一条真会坏的规则：**草稿非空绝不收**。「失焦就收回去」是最顺手的写法，
 * 而它把人刚写的一段话藏起来 —— 和这一批已经在楼内回复上修过一次的那类 bug 同一
 * 个形状（发失败时框和草稿都要留着）。
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
const createFeedbackComment = vi.fn()

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    createFeedbackComment: (...a: unknown[]) => createFeedbackComment(...a),
    getFeedback: (...a: unknown[]) => getFeedback(...a),
  }
})

import FeedbackDetailPage from './FeedbackDetailPage.vue'

// 一条没有任何评论的反馈：这一组用例只关心那个框，评论列表空着就行。
const DETAIL = {
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
} as unknown as FeedbackDetail

const CREATED = {
  id: 'c-1',
  parent_id: null,
  author_handle: 'alice',
  author_is_agent: false,
  author_avatar_id: null,
  body: '我也遇到了',
  reply_to_handle: null,
  likes: 0,
  liked: false,
  can_delete: false,
  reply_count: 0,
  replies_next_cursor: null,
  created_at: '2026-09-20T10:00:00Z',
}

async function mountPage() {
  const router = createRouter({
    history: createWebHashHistory(),
    routes: [{ path: '/feedback/:id', name: 'FeedbackDetail', component: FeedbackDetailPage }],
  })
  await router.push('/feedback/fb-1')
  await router.isReady()
  const vuetify = createVuetify({ components, directives })
  const Wrapper = {
    components: { FeedbackDetailPage },
    template: '<v-app><FeedbackDetailPage /></v-app>',
  }
  return render(Wrapper, { global: { plugins: [vuetify, router, createPinia()] } })
}

/** 等详情画出来（收起态那条是它的标志），再把元素交出去。
 *  收的是 `Element`：`render()` 给的 `baseElement` 就是这个类型，收窄成 `HTMLElement`
 *  会让每个调用点都报一次类型错（`as` 只留在取出来的那两个元素上）。 */
async function settled(baseElement: Element) {
  await waitFor(() => {
    expect(baseElement.querySelector('.fb-composer__open')).toBeTruthy()
  })
  return {
    toggle: baseElement.querySelector('.fb-composer__open') as HTMLElement,
    box: () => baseElement.querySelector('.fb-composer textarea') as HTMLTextAreaElement | null,
  }
}

beforeEach(() => {
  getFeedback.mockReset()
  createFeedbackComment.mockReset()
  getFeedback.mockResolvedValue(DETAIL)
})

describe('详情页的评论框', () => {
  it('一进来是收起态：一条按钮，没有多行框', async () => {
    const { baseElement } = await mountPage()
    const { box } = await settled(baseElement)

    expect(box()).toBeNull()
  })

  it('点一下展开，并且把光标送进框里', async () => {
    const { baseElement } = await mountPage()
    const { toggle } = await settled(baseElement)

    await fireEvent.click(toggle)

    // 「点一下就能打字」是收起态唯一的卖点：展开之后还要人再点一下框，这一条就白收了。
    await waitFor(() => {
      expect(baseElement.querySelector('.fb-composer textarea')).toBeTruthy()
    })
    await waitFor(() => {
      expect(document.activeElement?.tagName).toBe('TEXTAREA')
    })
  })

  it('空草稿失焦就收回去', async () => {
    const { baseElement } = await mountPage()
    const { toggle, box } = await settled(baseElement)

    await fireEvent.click(toggle)
    await waitFor(() => expect(box()).toBeTruthy())
    await fireEvent.blur(box()!)

    await waitFor(() => {
      expect(baseElement.querySelector('.fb-composer textarea')).toBeNull()
    })
  })

  it('草稿非空的失焦不收回，写的字还在', async () => {
    const { baseElement } = await mountPage()
    const { toggle, box } = await settled(baseElement)

    await fireEvent.click(toggle)
    await waitFor(() => expect(box()).toBeTruthy())
    await fireEvent.update(box()!, '我还想接着说两句')
    await fireEvent.blur(box()!)

    // 给「收回去」一个跑完的机会，再确认它没跑：不收是稳态，不能只断言「此刻还在」。
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(box()).toBeTruthy()
    expect(box()!.value).toBe('我还想接着说两句')
  })

  it('发表成功之后收回去，焦点回到收起态那条按钮上', async () => {
    createFeedbackComment.mockResolvedValue(CREATED)
    const { baseElement } = await mountPage()
    const { toggle, box } = await settled(baseElement)

    await fireEvent.click(toggle)
    await waitFor(() => expect(box()).toBeTruthy())
    await fireEvent.update(box()!, '我也遇到了')
    const submit = baseElement.querySelector('.fb-composer .v-btn') as HTMLElement
    await fireEvent.click(submit)

    await waitFor(() => {
      expect(createFeedbackComment).toHaveBeenCalledWith('fb-1', '我也遇到了', null)
    })
    // 发完把地方还回去。焦点不能掉在 body 上 —— 那个按钮跟着一起卸载了，键盘用户的
    // 下一个 Tab 会从页头重来。
    await waitFor(() => {
      expect(baseElement.querySelector('.fb-composer__open')).toBeTruthy()
    })
    expect(document.activeElement).toBe(baseElement.querySelector('.fb-composer__open'))
  })
})
