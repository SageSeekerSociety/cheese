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
