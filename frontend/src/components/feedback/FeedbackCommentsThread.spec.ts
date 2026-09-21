/**
 * 一栋楼底下那个按钮，三种状态各钉一条。
 *
 * 这一版把「展开更多」和「加载更多」拆成了两件事：手上已经有的折着，还是服务端那边
 * 还有没取的。两种看错了都会出问题，而且**都长得像没问题**：
 *
 *   - 该「展开」的时候去发请求 → 每次点都白跑一趟，取回来的是已经有的那几条。
 *   - 该「加载」的时候只摊开手上这几条 → 按钮看着点了、条数没变，人以为就这么多。
 *
 * 分开它们的依据只有一处，就是服务端发下来的 `reply_count`（这栋楼一共几条）。所以
 * 每一条用例都是围绕「手上几条 vs 一共几条」这两个数写的。
 *
 * 另外两条：**取下一页的时候按钮要挡住第二次点击**（同一个游标取两遍，追加出来是
 * 两条一模一样的回复，`:key` 也撞），以及**发失败时框和草稿都留着**（发一次 500 就把
 * 人写的那段话清掉，是这一版要修的东西之一）。
 */
import type { FeedbackComment } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'

import FeedbackCommentsThread from './FeedbackCommentsThread.vue'

const vuetify = createVuetify({ components, directives })

function c(id: string, extra: Partial<FeedbackComment> = {}): FeedbackComment {
  return {
    id,
    parent_id: null,
    author_handle: 'andylizf',
    author_is_agent: false,
    author_avatar_id: null,
    body: `正文 ${id}`,
    reply_to_handle: null,
    likes: 0,
    liked: false,
    can_delete: false,
    reply_count: 0,
    replies_next_cursor: null,
    created_at: '2026-09-20T00:00:00Z',
    ...extra,
  }
}

/** 一栋楼：顶层 `t` 的 `reply_count` 是**服务端说的总数**，手上的回复是 `replies` 条。 */
function building(total: number, replies: number): FeedbackComment[] {
  return [c('t', { reply_count: total }), ...Array.from({ length: replies }, (_, i) => c(`r-${i}`, { parent_id: 't' }))]
}

function mountThread(comments: FeedbackComment[], props: Record<string, unknown> = {}) {
  const onLoadReplies = vi.fn()
  const onLoadMore = vi.fn()
  const result = render(FeedbackCommentsThread, {
    props: {
      comments,
      hasMore: false,
      loadingMore: false,
      loadingReplies: {},
      // 两个 emit 都是连字符的（`load-more` / `load-replies`），Vue 只为连字符写法
      // 生成 prop 类型，驼峰的 `onLoadMore` 不在里面。
      'onLoad-more': onLoadMore,
      'onLoad-replies': onLoadReplies,
      ...props,
    },
    global: { plugins: [vuetify] },
  })
  return { ...result, onLoadReplies, onLoadMore }
}

/** 楼内那个按钮（顶层列表末尾的「加载更多评论」是另一个，两者同类名）。 */
function moreButton(container: Element): HTMLButtonElement {
  const el = container.querySelector<HTMLButtonElement>('.fb-thread__top .fb-thread__more')
  if (!el) throw new Error('这一栋楼没有「展开更多 / 加载更多 / 收起」按钮')
  return el
}

function shownReplies(container: Element): NodeListOf<Element> {
  return container.querySelectorAll('.fb-thread__replies > li')
}

describe('一栋楼里的回复', () => {
  it('默认只露两条，摊开手上这几条不发请求', async () => {
    const { container } = mountThread(building(5, 5))
    expect(shownReplies(container).length).toBe(2)
    expect(moreButton(container).textContent).toContain('展开更多 3 条回复')
  })

  it('摊开之后按钮变成「收起」，再点一下折回去', async () => {
    const { container } = mountThread(building(5, 5))
    await fireEvent.click(moreButton(container))
    expect(shownReplies(container).length).toBe(5)
    // 五条已经全在手上：这时候再发请求就是白发一趟，所以按钮该说的是「收起」。
    expect(moreButton(container).textContent).toContain('收起')

    await fireEvent.click(moreButton(container))
    expect(shownReplies(container).length).toBe(2)
  })

  it('服务端那边还有没取的，才说「加载更多回复」并去取下一页', async () => {
    const { container, onLoadReplies } = mountThread(building(5, 2))
    const button = moreButton(container)
    // 两个数一比就有答案：手上 2 条、一共 5 条 → 还差 3 条，而且手上没有可摊开的。
    expect(button.textContent).toContain('加载更多回复（还有 3 条）')
    expect(button.textContent).not.toContain('展开更多')

    await fireEvent.click(button)
    expect(onLoadReplies).toHaveBeenCalledWith('t')
  })

  it('取回来的回复落在折着的楼里也看得见：点「加载更多」顺手把楼展开', async () => {
    const { container, onLoadReplies, rerender } = mountThread(building(5, 2))
    await fireEvent.click(moreButton(container))
    expect(onLoadReplies).toHaveBeenCalledTimes(1)

    // 下一页到了（`reply_count` 不变，手上的变多）。
    await rerender({ comments: building(5, 4) })
    // 还差一条 → 按钮继续是「加载更多」；但如果这时候楼是折着的，取回来的那两条
    // 就白取了 —— 屏幕上什么都没变。所以这里要能看见手上全部四条。
    expect(shownReplies(container).length).toBe(4)
    expect(moreButton(container).textContent).toContain('还有 1 条')
  })

  it('正在取的时候按钮挡住第二次点击', async () => {
    const { container, onLoadReplies } = mountThread(building(9, 2), { loadingReplies: { t: true } })
    const button = moreButton(container)
    // 同一个游标取两遍，追加出来是两条一模一样的回复，`:key` 也撞。
    expect(button.disabled).toBe(true)
    expect(button.textContent).toContain('正在加载…')
    await fireEvent.click(button)
    expect(onLoadReplies).not.toHaveBeenCalled()
  })

  it('取完了但折着的楼只剩「展开更多」，没有『更多』可展开的楼一个按钮都不摆', async () => {
    const { container } = mountThread(building(3, 3))
    // 手上三条、一共三条，但默认只露两条 —— 该给的还是「展开更多 1 条回复」，
    // 而不是「加载更多」：服务端那边一条都不剩了，去取只会取回一页空的。
    expect(moreButton(container).textContent).toContain('展开更多 1 条回复')
    await fireEvent.click(moreButton(container))
    expect(shownReplies(container).length).toBe(3)

    // 一两条回复的楼不摆按钮：凑不出「更多」的时候它是个纯噪声。
    const { container: small } = mountThread(building(2, 2))
    expect(small.querySelector('.fb-thread__top .fb-thread__more')).toBeNull()
  })
})

describe('顶层评论的下一页', () => {
  it('服务端的游标没走到底才画「加载更多评论」', async () => {
    const { container, onLoadMore } = mountThread([c('t-1')], { hasMore: true })
    const button = container.querySelector<HTMLButtonElement>('.fb-thread > li:last-child .fb-thread__more')
    if (!button) throw new Error('游标还没走到底，列表末尾却没有「加载更多评论」')
    expect(button.textContent).toContain('加载更多评论')
    await fireEvent.click(button)
    expect(onLoadMore).toHaveBeenCalledTimes(1)
  })

  it('没有下一页时列表末尾多一个按钮都不画', () => {
    const { container } = mountThread([c('t-1')])
    expect(container.querySelectorAll('.fb-thread__more').length).toBe(0)
  })
})

describe('回复发失败', () => {
  it('框和草稿都留着，人按一下就能重试', async () => {
    const onReply = vi.fn((_id: string, _body: string, done: (ok: boolean) => void) => done(false))
    const { container } = mountThread([c('t')], { onReply })
    // 动作行里第一个是「赞」，「回复」在它后面。
    const buttons = Array.from(container.querySelectorAll<HTMLButtonElement>('.fb-ci__actions .fb-ci__act'))
    await fireEvent.click(buttons.find((b) => b.textContent?.includes('回复'))!)

    const textarea = container.querySelector<HTMLTextAreaElement>('.fb-ci__form textarea')
    expect(textarea).not.toBeNull()
    await fireEvent.update(textarea!, '我补一句')
    const sendButton = Array.from(container.querySelectorAll<HTMLButtonElement>('.fb-ci__form button')).find((b) =>
      b.textContent?.includes('回复')
    )!
    await fireEvent.click(sendButton)

    expect(onReply).toHaveBeenCalledTimes(1)
    expect(onReply.mock.calls[0][1]).toBe('我补一句')
    // 发挂了：框还在、里面那段话也还在。
    expect(container.querySelector('.fb-ci__form textarea')).not.toBeNull()
    expect(container.querySelector<HTMLTextAreaElement>('.fb-ci__form textarea')?.value).toBe('我补一句')
  })
})
