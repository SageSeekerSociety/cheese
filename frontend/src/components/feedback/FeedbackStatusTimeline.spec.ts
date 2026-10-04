/**
 * 部署管线把一条反馈推到「已上线」时没有推它的人，时间线上那一步只能靠说明告诉提交者
 * 是哪次改动 —— 所以说明要画出来，里面的 PR 地址要能点；人推的步骤没有说明，就什么也
 * 不多画。
 */
import type { FeedbackStatus, FeedbackTimelineEntry } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { beforeEach, describe, expect, it } from 'vitest'

import FeedbackStatusTimeline from './FeedbackStatusTimeline.vue'

import i18n, { setLocale } from '@/i18n'

const vuetify = createVuetify({ components, directives })
const LADDER: FeedbackStatus[] = ['received', 'in_progress', 'resolved', 'deployed']

function mountTimeline(timeline: FeedbackTimelineEntry[], status: FeedbackStatus) {
  return render(FeedbackStatusTimeline, {
    props: { timeline, status, ladder: LADDER },
    global: { plugins: [vuetify, i18n] },
  })
}

beforeEach(() => {
  setLocale('zh-CN')
})

describe('时间线上的说明', () => {
  it('部署管线那一步的说明画出来，PR 地址是一个链接', () => {
    const url = 'https://github.com/example/repo/pull/4321'
    const { container } = mountTimeline(
      [
        { status: 'received', by_handle: 'someone', at: '2026-09-20T00:00:00Z', note: null },
        { status: 'deployed', by_handle: null, at: '2026-09-21T00:00:00Z', note: `已由 PR #4321 修复并上线：${url}` },
      ],
      'deployed'
    )
    const notes = container.querySelectorAll('.fb-step__note')
    expect(notes).toHaveLength(1)
    expect(notes[0].textContent).toContain('已由 PR #4321 修复并上线')
    const link = notes[0].querySelector('a')
    expect(link?.getAttribute('href')).toBe(url)
    expect(link?.getAttribute('rel')).toContain('noopener')
  })

  it('人推的步骤没有说明，不多画一行', () => {
    const { container } = mountTimeline(
      [
        { status: 'received', by_handle: 'someone', at: '2026-09-20T00:00:00Z', note: null },
        { status: 'resolved', by_handle: 'admin', at: '2026-09-21T00:00:00Z', note: null },
      ],
      'resolved'
    )
    expect(container.querySelector('.fb-step__note')).toBeNull()
  })

  it('不是 http(s) 的东西不会变成链接', () => {
    const { container } = mountTimeline(
      [{ status: 'deployed', by_handle: null, at: '2026-09-21T00:00:00Z', note: 'javascript:alert(1)' }],
      'deployed'
    )
    expect(container.querySelector('.fb-step__note a')).toBeNull()
  })
})

describe('不修复', () => {
  it('梯子画到「不修复」就停，不再画「已修复」「已上线」', () => {
    const { container } = mountTimeline(
      [
        { status: 'received', by_handle: 'someone', at: '2026-09-20T00:00:00Z', note: null },
        { status: 'in_progress', by_handle: 'admin', at: '2026-09-21T00:00:00Z', note: null },
        { status: 'declined', by_handle: 'admin', at: '2026-09-22T00:00:00Z', note: null },
      ],
      'declined'
    )
    const titles = Array.from(container.querySelectorAll('.fb-step__title'), (el) => el.textContent?.trim())
    // 「处理中」那一档后面接了「不修复」，也就是已经走过去了；走过去的那一档
    // 换成「已处理」（它说的是「有人正在弄」，可这一步已经不在当前了）。
    expect(titles).toEqual(['已收录', '已处理', '不修复'])
    expect(container.querySelector('.fb-step--current .fb-step__title')?.textContent?.trim()).toBe('不修复')
  })
})

/**
 * 说明栏上的两种空档，说的都是记录，不是这件事做没做：当前这一步之后的写「未开始」；
 * 之前的说明它已经过去了、只是没单独留下时间 —— 后台能把一条反馈从「已收录」一步按到
 * 「已上线」；STATUS_LADDER 也允许直达，直达不补写中间那几行，那里写「未开始」和事实
 * 相反。断言行为、不锁具体用词。
 */
describe('没有记录的那些档', () => {
  const t = (key: string) => i18n.global.t(key)
  const count = (haystack: string, needle: string) => haystack.split(needle).length - 1

  it('一步按到「已上线」时，中间两档不说「未开始」', () => {
    const { container } = mountTimeline(
      [
        { status: 'received', by_handle: 'someone', at: '2026-09-20T00:00:00Z', note: null },
        { status: 'deployed', by_handle: 'andy', at: '2026-09-21T00:00:00Z', note: null },
      ],
      'deployed'
    )
    const text = container.textContent ?? ''
    expect(count(text, t('feedback.timeline.notStarted'))).toBe(0)
    expect(count(text, t('feedback.timeline.noRecord'))).toBe(2)
  })

  it('还没走到的档照旧说「未开始」', () => {
    const { container } = mountTimeline(
      [
        { status: 'received', by_handle: 'someone', at: '2026-09-20T00:00:00Z', note: null },
        { status: 'in_progress', by_handle: 'andy', at: '2026-09-21T00:00:00Z', note: null },
      ],
      'in_progress'
    )
    expect(count(container.textContent ?? '', t('feedback.timeline.notStarted'))).toBe(2)
  })
})

/**
 * 档位的名字分三副：还没轮到的不带「已」，正在这一步的和已经走过的各自一副。
 * 「处理中」走过去之后是「已处理」—— 它说的是「有人正在弄」，可这一步已经过去了。
 * 断的是「换上了另一套名字」，不锁具体用词。
 */
describe('档位的三副名字', () => {
  const t = (key: string) => i18n.global.t(key)
  const titles = (el: Element) => Array.from(el.querySelectorAll('.fb-step__title'), (n) => n.textContent?.trim())

  it('停在「处理中」时，后面两档用没走到的名字', () => {
    const { container } = mountTimeline(
      [
        { status: 'received', by_handle: 'someone', at: '2026-09-20T00:00:00Z', note: null },
        { status: 'in_progress', by_handle: 'andy', at: '2026-09-21T00:00:00Z', note: null },
      ],
      'in_progress'
    )
    expect(titles(container)).toEqual([
      t('feedback.status.received'),
      t('feedback.status.in_progress'),
      t('feedback.status.pending.resolved'),
      t('feedback.status.pending.deployed'),
    ])
    // 还没走到第三步：这一格是「正在处理」，不能提前说成「已处理」。
    expect(titles(container)[1]).not.toBe(t('feedback.status.passed.in_progress'))
  })

  it('走到「已上线」之后，四格都用走到的名字', () => {
    const { container } = mountTimeline(
      [
        { status: 'received', by_handle: 'someone', at: '2026-09-20T00:00:00Z', note: null },
        { status: 'deployed', by_handle: 'andy', at: '2026-09-21T00:00:00Z', note: null },
      ],
      'deployed'
    )
    expect(titles(container)).toEqual([
      t('feedback.status.received'),
      // 「处理中」说的是「有人正在弄」，可这条已经上线了：走过去的那一档换一副面孔。
      t('feedback.status.passed.in_progress'),
      t('feedback.status.resolved'),
      t('feedback.status.deployed'),
    ])
  })
})
