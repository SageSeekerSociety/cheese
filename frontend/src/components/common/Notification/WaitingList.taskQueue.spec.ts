/** 「等你处理」里出题人的那几行：通过申请和批改提交都去「参与者」页签。 */
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import WaitingList from './WaitingList.vue'

import i18n, { setLocale } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

const blank = { template: '<div />' }

function show(claims: number, reviews: number) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: blank },
      { path: '/spaces/:spaceId/tasks/:taskId/participants', name: 'TasksParticipants', component: blank },
      { path: '/spaces/:spaceId/tasks/:taskId/submissions', name: 'TasksSubmissions', component: blank },
    ],
  })
  return render(WaitingList, {
    props: {
      items: [
        {
          kind: 'task_queue',
          taskId: 88,
          taskName: '缓存模拟器',
          spaceId: 7,
          spaceName: '计算机系统',
          claims,
          reviews,
          at: '2026-10-07T10:00:00Z',
        },
      ],
      loading: false,
      failed: false,
      errorReason: '',
      actionError: '',
      acting: null,
      clearing: null,
      replying: null,
    },
    global: { plugins: [router, createVuetify({ components, directives }), i18n] },
  })
}

describe('题目上等你处理的', () => {
  it('只有提交待评审时也去「参与者」页签', () => {
    const view = show(0, 3)

    view.getByText(i18n.global.t('home.inbox.task.reviews', { n: 3 }))
    expect(view.getByText('缓存模拟器').closest('a')?.getAttribute('href')).toBe('/spaces/7/tasks/88/participants')
  })

  it('有申请待通过时去「参与者」页签', () => {
    const view = show(2, 0)

    view.getByText(i18n.global.t('home.inbox.task.claims', { n: 2 }))
    expect(view.getByText('缓存模拟器').closest('a')?.getAttribute('href')).toBe('/spaces/7/tasks/88/participants')
  })
})
