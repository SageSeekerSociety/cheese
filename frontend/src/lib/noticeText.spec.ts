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

import { collapseNotices } from './platformNotice'

import i18n, { setLocale } from '@/i18n'

const vuetify = createVuetify({ components, directives })

// What the backend stores for a ready PR: the Chinese line, and beside it the
// sentence's key and parameters.
function readyLine(): Block {
  return {
    id: 'b1',
    topic_id: 't',
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
