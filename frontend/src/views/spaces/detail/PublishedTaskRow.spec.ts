// 「我发布的」那一行说的人数要和这道题怎么开始对得上：直接开始的题没有人申请，
// 不写「申请 N」；申请制的题照旧写。
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
import dayjs from 'dayjs'
import { afterEach, beforeAll, describe, expect, it } from 'vitest'

import PublishedTaskRow from './PublishedTaskRow.vue'

import i18n, { setLocale } from '@/i18n'

const stub = { render: () => null }

function row(startMode: 'DIRECT' | 'APPLY', deadline: number | null = null) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/spaces/:spaceId/tasks/:taskId', name: 'TasksDetail', component: stub },
      { path: '/spaces/:spaceId/tasks/:taskId/participants', name: 'TasksParticipants', component: stub },
    ],
  })
  return render(PublishedTaskRow, {
    props: {
      spaceId: 7,
      task: {
        taskId: 42,
        taskName: 'B 树的插入与删除',
        startMode,
        category: { id: 3, name: '数据结构' },
        approved: 'APPROVED',
        visibilityStatus: 'APPROVED_VISIBLE',
        isVisible: true,
        createdAt: Date.now(),
        publishedAt: Date.now(),
        endedAt: null,
        deadline,
        participantLimit: null,
        participantCount: 1,
        approvedParticipantCount: 1,
        pendingParticipantApprovalCount: 0,
        submittedParticipantCount: 0,
        pendingReviewCount: 0,
        successfulParticipantCount: 0,
        failedParticipantCount: 0,
        submissionConversionRate: 0,
        successRate: 0,
        latestSubmissionAt: null,
      } as never,
    },
    global: { plugins: [createVuetify({ components, directives }), i18n, router] },
  })
}

describe('我发布的题目行', () => {
  beforeAll(() => setLocale('zh-CN'))
  afterEach(() => cleanup())

  it('直接开始的题：只说参与了几个，不说申请', () => {
    const { container } = row('DIRECT')

    expect(container.textContent).toContain('参与 1')
    expect(container.textContent).not.toContain('申请')
  })

  it('申请制的题：申请了几个、参与了几个都说', () => {
    const { container } = row('APPLY')

    expect(container.textContent).toContain('申请 1')
    expect(container.textContent).toContain('参与 1')
  })

  // 出题人手上没有领取：申请制的题写的是申请截止，直接开始的题照旧。
  it.each([
    ['APPLY', 'spaces.detail.tasks.row.applyDeadline'],
    ['DIRECT', 'spaces.detail.tasks.row.deadline'],
  ] as const)('%s 的题：截止那一格说清是哪一个截止', (startMode, key) => {
    const at = Date.UTC(2026, 10, 2, 4)
    const view = row(startMode, at)

    view.getByText(i18n.global.t(key, { date: dayjs(at).format(i18n.global.t('tasks.page.dateFormat')) }))
  })
})
