// 主线上那一行：支线里的一轮没回答就出错了，说出错；是额度用完拦下的，说额度用完——
// 那不是重试能解决的「回复失败」。
import type { ThreadSummary } from '@/types/threads'

import { createVuetify } from 'vuetify'
import { render } from '@testing-library/vue'
import { beforeEach, describe, expect, it } from 'vitest'

import ThreadLine from './ThreadLine.vue'

import { setLocale } from '@/i18n'

function summary(extra: Partial<ThreadSummary>): ThreadSummary {
  return {
    id: 't1',
    room_id: 'room1',
    root_block_id: 'root',
    reply_count: 0,
    last_reply_at: null,
    last_reply: null,
    participants: ['lin'],
    tasks: [],
    ...extra,
  }
}

function show(s: ThreadSummary) {
  return render(ThreadLine, {
    props: { summary: s, replying: null, refs: {} as never, nameOf: (h: string) => h, time: null },
    global: { plugins: [createVuetify()] },
  })
}

beforeEach(() => setLocale('zh-CN'))

describe('支线出错时主线上那一行', () => {
  it('额度用完拦下的一轮说额度已用完，不说回复失败', () => {
    const view = show(summary({ failed: true, failed_code: 'credits_exhausted' }))
    expect(view.getByText('额度已用完')).toBeTruthy()
    expect(view.queryByText('回复失败')).toBeNull()
  })

  it('别的原因出错还是回复失败', () => {
    const view = show(summary({ failed: true, failed_code: null }))
    expect(view.getByText('回复失败')).toBeTruthy()
  })
})
