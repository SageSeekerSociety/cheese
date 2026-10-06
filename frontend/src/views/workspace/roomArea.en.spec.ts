/** 界面切到英文时，话题与房间这一片不能再漏出中文。
 *
 * 每个组件用英文界面渲染一遍，断言渲染结果（含弹到 body 上的部分）里一个汉字都没有。
 * 夹具数据本身用英文写：这里要抓的是写死在界面里的字，不是用户或后端给的内容。
 */
import type { Component } from 'vue'
import type { AcceptCard, Topic } from '@/cx_types'
import type { VisibleRow } from '@/lib/topicTree'

import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/me', () => ({ myHandle: () => 'alice' }))

import ProjectAccessNotice from './ProjectAccessNotice.vue'

import AcceptGateFace from '@/components/accept/AcceptGateFace.vue'
import MailDraftCard from '@/components/room/MailDraftCard.vue'
import TopicRailRow from '@/components/topic-sidebar/TopicRailRow.vue'
import i18n, { setLocale } from '@/i18n'
import { stallReasonText, waitText } from '@/lib/replyWait'
import { topicStateBadge } from '@/lib/topicState'

const CJK = /[㐀-䶿一-鿿豈-﫿]/
const vuetify = createVuetify({ components, directives })
const UserRef = { props: ['handle', 'name'], template: '<span>{{ name || handle }}</span>' }

function mount(component: Component, props: Record<string, unknown>) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/:any(.*)*', component: { template: '<div />' } }],
  })
  return render(component, {
    props,
    global: { plugins: [router, vuetify, i18n], stubs: { UserRef, UserRefLink: UserRef } },
  })
}

function expectNoChinese() {
  const text = document.body.textContent ?? ''
  const titles = Array.from(document.body.querySelectorAll('[title],[aria-label],[placeholder]')).flatMap((el) =>
    ['title', 'aria-label', 'placeholder'].map((a) => el.getAttribute(a) ?? '')
  )
  expect([text, ...titles].filter((s) => CJK.test(s))).toEqual([])
}

const topic = (over: Partial<Topic> = {}) =>
  ({ id: 't1', title: 'Launch plan', status: 'active', parent_id: 'root', ...over }) as unknown as Topic

beforeEach(() => {
  setActivePinia(createPinia())
  setLocale('en')
})
afterEach(() => {
  cleanup()
  setLocale('zh-CN')
})

describe('topic and room surfaces in English', () => {
  it.each(['unauthenticated', 'forbidden', 'archived'] as const)('project access notice: %s', (reason) => {
    mount(ProjectAccessNotice, { reason })
    expectNoChinese()
  })

  it('topic rail row with a stalled member, collapsed, draft topic', () => {
    const now = Date.parse('2026-09-28T04:00:00Z')
    const wait = { member: 'cheese-a1', reason: 'mention', since: '2026-09-28T00:00:00Z' }
    const row = {
      topic: topic({ status: 'draft', waits: [wait] }),
      depth: 1,
      hasChildren: true,
      collapsed: true,
      hiddenCount: 3,
      hiddenUnread: 2,
      unreadTotal: 2,
      hiddenWorking: true,
      hiddenAwaits: false,
      hiddenStalled: false,
    } as unknown as VisibleRow<Topic>
    mount(TopicRailRow, {
      row,
      selected: false,
      page: false,
      renaming: false,
      menuOpen: false,
      stalled: true,
      marks: [
        { handle: 'cheese-a1', name: 'Moss', agent: true, state: 'stalled', title: waitText(wait, 'Moss', now) },
        { handle: 'cheese-b2', name: 'Fern', agent: true, state: 'working', title: 'Fern is working here' },
      ],
      toggleTitle: 'Expand',
      actions: () => [],
    })
    expectNoChinese()
  })

  it('accept card gate faces', () => {
    for (const status of ['gate_failed', 'gate_blocked']) {
      mount(AcceptGateFace, {
        card: { status, gate_output: '' } as unknown as AcceptCard,
        open: true,
        agentName: 'Moss',
        agentHandle: 'moss',
      })
      expectNoChinese()
      cleanup()
    }
  })

  it('mail draft card, for the owner and for someone else', () => {
    const mail = {
      draftId: 'd1',
      owner: 'alice',
      account: 'me@example.com',
      to: ['a@example.com', 'b@example.com'],
      cc: ['c@example.com'],
      subject: '',
      body: 'Hello',
      attachments: [{ name: 'summary.md', size: 2074 }],
    }
    mount(MailDraftCard, { mail, outcome: null, me: 'alice' })
    expectNoChinese()
    cleanup()
    mount(MailDraftCard, { mail, outcome: { status: 'failed', sentAt: null, reason: null }, me: 'bob' })
    expectNoChinese()
  })

  it('stall reasons and topic phase labels', () => {
    const now = Date.parse('2026-09-28T16:00:00Z')
    for (const reason of [
      'device_waiting',
      'machine_provisioning',
      'sandbox_rebuilt',
      'check',
      'conflict',
      'rejected',
      'gate',
      'mention',
      null,
    ]) {
      const text = stallReasonText({ reason, since: '2026-09-28T12:00:00Z', pr: 12 }, 'Moss', now)
      expect(text).not.toMatch(CJK)
    }
    expect(waitText({ member: 'cheese-a1', reason: 'failed', since: '2026-09-28T12:00:00Z' }, 'Moss', now)).not.toMatch(
      CJK
    )
    for (const status of ['archived', 'closed', 'draft', 'active']) {
      expect(topicStateBadge(status).label).not.toMatch(CJK)
    }
  })
})
