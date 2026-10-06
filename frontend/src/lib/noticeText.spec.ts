// 平台在房间里说的那一行，跟着读者选的语言走 —— 已经在屏幕上的行，切换语言当场重画。
import type { Component } from 'vue'
import type { Block } from '@/cx_types'
import type { Notification } from '@/network/api/notifications/types'

import { computed, defineComponent, h, nextTick } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import NotificationItem from '../components/common/Notification/NotificationItem.vue'
import RoomNotice from '../components/room/RoomNotice.vue'

import { renderNoticeMessage } from './noticeText'
import { collapseNotices } from './platformNotice'

import i18n, { setLocale } from '@/i18n'

const vuetify = createVuetify({ components, directives })

// What the backend stores for a ready PR: the Chinese line, and beside it the
// sentence's key and parameters.
function readyLine(): Block {
  return {
    id: 'b1',
    conversation_id: 't',
    kind: 'event',
    author_type: 'platform',
    author: 'accept',
    content: 'PR #41 可以合并了，等 alice 采纳',
    created_at: '2026-09-30T08:00:00Z',
    meta: {
      event_type: 'accept_ready',
      severity: 'info',
      who: 'human',
      detail: '这个 PR 满足项目的合并规则，采纳即当场合并。\nhttps://example.test/pr/41',
      detail_label: '下一步',
      i18n: {
        content: { key: 'acceptReady', params: { pr: 41, reviewer: 'alice' } },
        detail: { key: 'acceptReadyDetail', params: { url: 'https://example.test/pr/41' } },
        detail_label: { key: 'labelNextStep', params: {} },
      },
    },
  } as unknown as Block
}

// Descriptors stored before ordinary comments stopped scheduling agent turns.
// Replay these payloads, rather than asking the backend to generate them again.
const historicalComments = [
  {
    key: 'docCommented',
    params: { actor: 'ana😀' },
    chinese: 'ana😀 评论了文档',
    english: 'ana😀 commented on the doc',
  },
  {
    key: 'docCommentedHandedTo',
    params: { actor: 'ana😀', seat: '<@cheese-test>' },
    chinese: 'ana😀 评论了文档，已交给 <@cheese-test>',
    english: 'ana😀 commented on the doc; handed to <@cheese-test>',
  },
]

// The room builds its rows in a computed, so a language switch rebuilds them;
// mounting through one here keeps that part of the path under test.
function mountLine(block: Block) {
  const Room = defineComponent({
    setup() {
      const row = computed(() => collapseNotices([block])[0])
      return () =>
        h(RoomNotice as Component, {
          block: row.value.block,
          notice: row.value.notice!,
          run: row.value.run,
          name: '芝士',
          time: '16:05',
          agentName: '芝士',
          refs: { mentionNames: {}, topicTitles: {} },
        })
    },
  })
  return render(Room, { global: { plugins: [vuetify, i18n] } })
}

beforeEach(() => setLocale('zh-CN'))
afterEach(() => setLocale('zh-CN'))

describe('a platform line in the room', () => {
  it.each(historicalComments)('replays historical $key in both reader languages', async (old) => {
    const block = JSON.parse(
      JSON.stringify({
        ...readyLine(),
        content: old.chinese,
        meta: {
          i18n: { content: { key: old.key, params: old.params } },
        },
      })
    ) as Block
    const view = mountLine(block)
    // Room mentions render their handle without the descriptor's angle brackets.
    const visible = (text: string) => text.replace('<@cheese-test>', '@cheese-test')
    expect(view.container.textContent).toContain(visible(old.chinese))
    setLocale('en')
    await nextTick()
    expect(view.container.textContent).toContain(visible(old.english))
    expect(view.container.textContent).not.toContain(visible(old.chinese))
    setLocale('zh-CN')
    await nextTick()
    expect(view.container.textContent).toContain(visible(old.chinese))
  })

  it('re-renders in English when the reader switches language', async () => {
    const view = mountLine(readyLine())
    expect(view.getByText('PR #41 可以合并了，等 alice 采纳')).toBeTruthy()

    setLocale('en')
    await nextTick()

    expect(view.getByText('PR #41 is ready to merge, waiting for alice to accept')).toBeTruthy()
    expect(view.queryByText('PR #41 可以合并了，等 alice 采纳')).toBeNull()
    expect(view.getByText('Next step')).toBeTruthy()
  })

  it('renders a sentence chosen inside a sentence, and a list of them, in English', async () => {
    setLocale('en')
    const block = {
      ...readyLine(),
      content: '这次交付新建了产物《报告》，此前项目里没有这一项',
      meta: {
        event_type: 'artifact_declared',
        who: 'human',
        detail: '《报告》 第 2 版\n《数据》 尚未交付',
        detail_label: '项目现在的产物清单',
        i18n: {
          content: { key: 'artifactDeclared', params: { name: 'Report' } },
          detail: {
            key: 'lines',
            params: {
              items: [
                { key: 'artifactVersion', params: { name: 'Report', version: 2 } },
                { key: 'artifactUndelivered', params: { name: 'Data' } },
              ],
            },
          },
          detail_label: { key: 'labelArtifactList', params: {} },
        },
      },
    } as unknown as Block
    const view = mountLine(block)
    await nextTick()
    expect(view.getByText(/This delivery created a new artifact, “Report”/)).toBeTruthy()
    expect(view.container.textContent).toContain('“Report” version 2\n“Data” not delivered yet')
  })

  it('shows the stored text for a line written before lines had a key', async () => {
    setLocale('en')
    const meta = { ...(readyLine().meta as Record<string, unknown>) }
    delete meta.i18n
    const view = mountLine({ ...readyLine(), meta } as Block)
    await nextTick()
    expect(view.getByText('PR #41 可以合并了，等 alice 采纳')).toBeTruthy()
  })
})

describe('the same line in Activity', () => {
  it.each(historicalComments)('replays historical $key with its stored parameters', async (old) => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: {} },
        { path: '/projects/:projectId/topics/:topicId', name: 'workspace-topic', component: {} },
      ],
    })
    await router.push('/')
    const notice = JSON.parse(
      JSON.stringify({
        id: 19,
        type: 'ROOM_NOTICE',
        read: false,
        createdAt: Date.now(),
        entities: {},
        contextMetadata: {
          projectId: 'p',
          topicId: 't',
          topicTitle: '历史评论',
          content: old.chinese,
          message: { key: old.key, params: old.params },
          eventType: '',
          severity: 'info',
        },
      })
    ) as Notification
    const view = render(NotificationItem, {
      props: { notification: notice, onMarkAsRead: () => {}, onDelete: () => {} },
      global: { plugins: [vuetify, router, i18n] },
    })
    await waitFor(() => expect(view.getByText(old.chinese)).toBeTruthy())
    setLocale('en')
    await waitFor(() => expect(view.getByText(old.english)).toBeTruthy())
    expect(view.queryByText(old.chinese)).toBeNull()
    setLocale('zh-CN')
    await waitFor(() => expect(view.getByText(old.chinese)).toBeTruthy())
  })

  it('follows the reader’s language too', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: {} },
        { path: '/projects/:projectId/topics/:topicId', name: 'workspace-topic', component: {} },
      ],
    })
    await router.push('/')
    const notice: Notification = {
      id: 9,
      type: 'ROOM_NOTICE',
      read: false,
      createdAt: Date.now(),
      entities: {},
      contextMetadata: {
        projectId: 'p',
        topicId: 't',
        topicTitle: 'issue 1086',
        content: 'PR #41 可以合并了，等 alice 采纳',
        message: { key: 'acceptReady', params: { pr: 41, reviewer: 'alice' } },
        eventType: 'accept_ready',
        severity: 'info',
      },
    }
    const view = render(NotificationItem, {
      props: { notification: notice, onMarkAsRead: () => {}, onDelete: () => {} },
      global: { plugins: [vuetify, router, i18n] },
    })
    await waitFor(() => expect(view.getByText('PR #41 可以合并了，等 alice 采纳')).toBeTruthy())

    setLocale('en')
    await waitFor(() => expect(view.getByText('PR #41 is ready to merge, waiting for alice to accept')).toBeTruthy())
    expect(view.getByText('In "issue 1086"')).toBeTruthy()
  })
})

// 句子里的一串（几个房间、几个文件）存成条目本身，由读者的屏幕按语言连起来、加引号。
describe('a list said inside a sentence', () => {
  const collided = {
    key: 'migrationCollisionDetail',
    params: { rooms: { list: ['Pricing', 'Search', 'Billing'], quoted: true } },
  }

  it('in English is joined with commas and "and", each item in English quotes', () => {
    setLocale('en')
    expect(renderNoticeMessage(collided, '')).toMatch(/^The other change is in: “Pricing”, “Search”, and “Billing”\./)
  })

  it('in Chinese is joined with 、, each item in 「」, as the stored sentence says it', () => {
    setLocale('zh-CN')
    expect(renderNoticeMessage(collided, '')).toMatch(/^另一项改动在：「Pricing」、「Search」、「Billing」。/)
  })

  it('without quotes, and with a sentence as an item, each item renders in the reader language', () => {
    setLocale('en')
    const edited = { key: 'docEdited', params: { actor: { list: ['<@alice>', { key: 'actorCheese', params: {} }] } } }
    expect(renderNoticeMessage(edited, '')).toBe('<@alice> and Cheese edited the doc')
    const missing = { key: 'revisionNumbersMissing', params: { rows: { list: [3, 5] }, total: 4 } }
    expect(renderNoticeMessage(missing, '')).toMatch(/^No tracked change numbered 3 and 5\./)
  })

  it('a plain parameter still shows as it was sent', () => {
    setLocale('en')
    expect(renderNoticeMessage({ key: 'docEdited', params: { actor: 'alice' } }, '')).toBe('alice edited the doc')
  })
})
