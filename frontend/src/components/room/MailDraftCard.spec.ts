/** 邮件草稿在房间里确认：主人有按钮，别人只看到在等谁；结果回来就变终态。 */
import type { Component } from 'vue'
import type { Block } from '@/cx_types'
import type { MailOutcome } from '../../lib/platformNotice'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { collapseNotices, mailOutcomes } from '../../lib/platformNotice'

import MailDraftCard from './MailDraftCard.vue'

vi.mock('../../api', () => ({
  sendMailDraft: vi.fn(async () => ({ draft: { sent_at: '2026-09-27T08:30:00Z' }, refused: [], notes: [] })),
  discardMailDraft: vi.fn(async () => ({})),
}))

import { discardMailDraft, sendMailDraft } from '../../api'

import { setLocale } from '@/i18n'

// These assertions read the Chinese copy.
beforeEach(() => {
  setLocale('zh-CN')
})

const vuetify = createVuetify({ components, directives })

function drafted(id: string, subject = 'Re: 芝士测试'): Block {
  return {
    id: `b-${id}`,
    conversation_id: 't',
    kind: 'event',
    author_type: 'platform',
    author: 'system',
    content: '芝士在 me@qq.com 里写好了一封草稿，等 chiruotong 确认后才会发送',
    created_at: '2026-09-27T08:19:00Z',
    meta: {
      event_type: 'mail_drafted',
      severity: 'info',
      who: 'cheese',
      detail: `收件人：a@ruc.edu.cn\n主题：${subject}`,
      mail_draft_id: id,
      mail: {
        owner: 'chiruotong',
        account: 'me@qq.com',
        to: ['a@ruc.edu.cn'],
        cc: [],
        subject,
        body: '你好，摘要见附件。',
        attachments: [{ name: '摘要.md', size: 2074 }],
      },
    },
  } as unknown as Block
}

function mount(
  me: string | null,
  outcome = null as ReturnType<typeof mailOutcomes> extends Map<string, infer V> ? V | null : never
) {
  const [row] = collapseNotices([drafted('d1')])
  expect(row.notice?.mode).toBe('mail-draft')
  if (row.notice?.mode !== 'mail-draft') throw new Error('not a mail card')
  const mail = row.notice.mail
  return render(MailDraftCard as Component, { props: { mail, outcome, me }, global: { plugins: [vuetify] } })
}

beforeEach(() => vi.clearAllMocks())

describe('房间里的邮件确认卡片', () => {
  it('邮箱主人看到全文和「确认发送」，点了就发、卡片变成已发送', async () => {
    const { getByText, getByTestId } = mount('chiruotong')
    getByText('a@ruc.edu.cn')
    getByText('摘要.md')
    await fireEvent.click(getByText('确认发送'))
    expect(sendMailDraft).toHaveBeenCalledWith('d1')
    await waitFor(() => expect(getByTestId('mail-state').textContent).toContain('已发送'))
  })

  it('主人也可以放弃', async () => {
    const { getByText, getByTestId } = mount('chiruotong')
    await fireEvent.click(getByText('放弃'))
    expect(discardMailDraft).toHaveBeenCalledWith('d1')
    await waitFor(() => expect(getByTestId('mail-state').textContent).toContain('已放弃'))
  })

  it('别人看不到按钮，只看到在等谁', () => {
    const { queryByText, getByTestId } = mount('someone-else')
    expect(queryByText('确认发送')).toBeNull()
    expect(getByTestId('mail-waiting').textContent).toContain('chiruotong')
  })

  it('房间里记下的结果让卡片变成终态', () => {
    const { queryByText, getByTestId } = mount('chiruotong', {
      status: 'failed',
      sentAt: null,
      reason: '服务器拒绝',
    })
    expect(queryByText('确认发送')).toBeNull()
    expect(getByTestId('mail-state').textContent).toContain('服务器拒绝')
  })
})

describe('房间事件', () => {
  it('两封草稿各是一张卡，不折在一起；结果按草稿对上', () => {
    const rows = collapseNotices([drafted('d1'), drafted('d2', '第二封')])
    expect(rows.filter((r) => r.notice?.mode === 'mail-draft')).toHaveLength(2)

    const ends = mailOutcomes([
      {
        ...drafted('x'),
        meta: { event_type: 'mail_result', mail_draft_id: 'd2', status: 'sent', sent_at: '2026-09-27T08:30:00Z' },
      } as unknown as Block,
    ])
    expect(ends.get('d2')?.status).toBe('sent')
    expect(ends.has('d1')).toBe(false)
  })

  it('草稿卡片自己带着后来的下落，时间线不用另外对', () => {
    const result = {
      ...drafted('x'),
      id: 'r1',
      meta: { event_type: 'mail_result', mail_draft_id: 'd2', status: 'failed', detail: '服务器拒绝' },
    } as unknown as Block
    const cards = collapseNotices([drafted('d1'), drafted('d2', '第二封'), result]).filter(
      (r) => r.notice?.mode === 'mail-draft'
    )
    const outcomes = cards.map((r) => (r.notice?.mode === 'mail-draft' ? r.notice.outcome?.status ?? null : 'x'))
    expect(outcomes).toEqual([null, 'failed'])
  })
})
