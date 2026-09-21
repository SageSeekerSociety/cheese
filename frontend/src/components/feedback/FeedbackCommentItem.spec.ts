/**
 * 一条评论上的三件事，每一件都是「看着对、其实没接上」的那种。
 *
 * 1. **「回复 X」只在数据里有时才画。** 这一句上一版的注释里承诺过（模板里没有），
 *    而它能不能画出来完全取决于服务端有没有把 `reply_to_handle` 存下来 —— 折楼之后
 *    `parent_id` 只指顶层，「回的是谁」在客户端猜不出来。所以这里钉的是「有值就画、
 *    没值就不画」，顶层评论恒为没值。
 * 2. **点赞的激活态是三个信号一起变**（图标实心、文案变「已赞」、底色出现），不是只
 *    换个颜色：只靠颜色的话，色觉障碍的读者看不出自己点没点过。任何一个信号掉了都
 *    是这条测试该红的时候。
 * 3. **删除按钮由服务端的 `can_delete` 说了算，确认那句要说清连带什么。** 删顶层会连
 *    它下面的回复一起删，只说「删掉这条评论」而实际删掉一栋楼是在骗按按钮的人。
 *
 * 查询一律用 `within(container)`：动作行里「回复」和「删除」都不是唯一的文字
 * （「回复 X」那一行、确认行的「确认删除」里都有），不圈定范围的话拿到的是
 * 「Found multiple elements」而不是我们想断言的那一件。
 */
import type { FeedbackComment } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, within } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import FeedbackCommentItem from './FeedbackCommentItem.vue'

const vuetify = createVuetify({ components, directives })

function c(extra: Partial<FeedbackComment> = {}): FeedbackComment {
  return {
    id: 'c-1',
    parent_id: null,
    author_handle: 'andylizf',
    author_is_agent: false,
    author_avatar_id: null,
    body: '复现了。',
    reply_to_handle: null,
    likes: 0,
    liked: false,
    can_delete: false,
    // 分页那两列是服务端随每条评论一起发下来的：这一条自己那一栋有几条回复、以及
    // 它那一栋的下一页从哪开始。用不到它们的用例不必各写一遍。
    reply_count: 0,
    replies_next_cursor: null,
    created_at: '2026-09-20T00:00:00Z',
    ...extra,
  }
}

function mountComment(extra: Partial<FeedbackComment> = {}, props: Record<string, unknown> = {}) {
  const result = render(FeedbackCommentItem, {
    props: { comment: c(extra), replying: false, replyCount: 0, ...props },
    global: { plugins: [vuetify] },
  })
  // container 在 @testing-library/vue 的类型里是 Element，而 within() 要
  // HTMLElement —— 它拿到的本来就一定是元素，转一下比在每处查询外面套 as 干净。
  return { ...result, ui: within(result.container as HTMLElement) }
}

describe('楼内回复指代', () => {
  it('存了回复对象就把「回复 X」画出来', () => {
    const { container } = mountComment({ parent_id: 'c-0', reply_to_handle: 'andylizf' })
    const line = container.querySelector('.fb-ci__re')
    expect(line, '存了 reply_to_handle 却没画「回复 X」').not.toBeNull()
    expect(line?.textContent).toContain('andylizf')
  })

  it('顶层评论一个字都不画', () => {
    const { container } = mountComment()
    expect(container.querySelector('.fb-ci__re')).toBeNull()
  })
})

describe('点赞', () => {
  it('点过和没点过是三个信号一起变，不是只换个颜色', () => {
    const off = mountComment({ likes: 3 })
    // 图标：外框 → 实心
    expect(off.container.querySelector('.mdi-thumb-up-outline')).not.toBeNull()
    expect(off.container.querySelector('.mdi-thumb-up')).toBeNull()
    expect(off.ui.getByText('赞')).toBeTruthy()
    expect(off.container.querySelector('.fb-ci__like.is-on')).toBeNull()

    const on = mountComment({ likes: 4, liked: true })
    expect(on.container.querySelector('.mdi-thumb-up')).not.toBeNull()
    expect(on.container.querySelector('.mdi-thumb-up-outline')).toBeNull()
    expect(on.ui.getByText('已赞')).toBeTruthy()
    // 底色（中性 tonal，不是琥珀）—— 颜色是三个信号里最弱的那一个，但也要在。
    expect(on.container.querySelector('.fb-ci__like.is-on')).not.toBeNull()
  })

  it('没人赞过就不摆一个「0」出来', () => {
    const { ui } = mountComment({ likes: 0 })
    expect(ui.queryByText('0')).toBeNull()
  })

  it('有人赞过就把计数摆出来', () => {
    const { ui } = mountComment({ likes: 3 })
    expect(ui.getByText('3')).toBeTruthy()
  })

  it('按下去只 emit 一个 id，不自己发请求', async () => {
    const { ui, emitted } = mountComment({ likes: 3 })
    await fireEvent.click(ui.getByText('赞'))
    expect(emitted().like).toEqual([['c-1']])
  })
})

describe('删除', () => {
  it('服务端说不能删就不画那个按钮', () => {
    const { ui } = mountComment({ can_delete: false })
    expect(ui.queryByText('删除')).toBeNull()
  })

  it('删顶层时说要连带几条回复', async () => {
    const { ui } = mountComment({ can_delete: true }, { replyCount: 2 })
    await fireEvent.click(ui.getByText('删除'))
    expect(ui.getByText('删掉这条评论，连同它下面的 2 条回复一起？')).toBeTruthy()
  })

  it('没有回复就不提连带', async () => {
    const { ui } = mountComment({ can_delete: true }, { replyCount: 0 })
    await fireEvent.click(ui.getByText('删除'))
    expect(ui.getByText('删掉这条评论？')).toBeTruthy()
  })

  it('确认之后才 emit', async () => {
    const { ui, emitted } = mountComment({ can_delete: true })
    await fireEvent.click(ui.getByText('删除'))
    await fireEvent.click(ui.getByText('确认删除'))
    expect(emitted().remove).toEqual([['c-1']])
  })

  it('取消则什么都不发，动作行回到原样', async () => {
    const { ui, emitted } = mountComment({ can_delete: true })
    await fireEvent.click(ui.getByText('删除'))
    await fireEvent.click(ui.getByText('取消'))
    expect(emitted().remove).toBeUndefined()
    expect(ui.getByText('删除')).toBeTruthy()
  })
})
