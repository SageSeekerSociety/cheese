/** 待办页「等你处理」那半张画面（`InboxViewView.vue`）：只收 props、只发事件。
 *
 *  这一份守的正是「它只认 props」这句话：**不装 pinia、不接任何 store、不接任何接口**，
 *  只给一串 props 就挂得起来，并且点下去的每一下都变成往上发的事件 —— 去哪儿取数、写回
 *  后端、跳地址全在容器 `InboxView.vue` 里。清单本身长什么样、按什么分组、每一件打开哪
 *  一条去处（任务 / 支线 / 频道 / 项目总览，带不带 `block`）也在这里锁住。
 */
import type { Component } from 'vue'
import type { WaitingItem } from '@/types/waiting'

import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it } from 'vitest'

import InboxViewView from './InboxViewView.vue'

import { setLocale, t } from '@/i18n'

const View = InboxViewView as unknown as Component

/** 一件「等你处理」：`types/waiting.ts` 的 `WaitingItem`。 */
function item(over: Partial<WaitingItem> = {}): WaitingItem {
  return {
    projectId: 'p1',
    projectName: '课程项目',
    topicId: 'c1',
    topicTitle: '前端',
    taskId: null,
    taskTitle: null,
    taskTitleSource: null,
    phrase: 'awaiting_review',
    reason: 'reviewer',
    at: '2026-10-07T10:00:00Z',
    ...over,
  }
}

/** 一份齐全的 props：每一格只盖自己要变的那几个。 */
function props(over: Record<string, unknown> = {}) {
  return {
    noProjects: false,
    loading: false,
    failed: false,
    errorReason: '',
    items: [] as WaitingItem[],
    actionError: '',
    acting: null as number | null,
    clearing: null as string | null,
    ...over,
  }
}

let vuetify: ReturnType<typeof createVuetify>
let router: ReturnType<typeof createRouter>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(async () => {
  setLocale('zh-CN')
  router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/teams', name: 'HomeTeamsExplore', component: { template: '<div />' } },
      { path: '/p/:projectId/t/:topicId', name: 'workspace-topic', component: { template: '<div />' } },
      { path: '/p/:projectId/t/:topicId/tasks/:taskId', name: 'workspace-task', component: { template: '<div />' } },
      {
        path: '/p/:projectId/c/:topicId/threads/:threadId',
        name: 'workspace-thread',
        component: { template: '<div />' },
      },
      { path: '/p/:projectId/overview', name: 'workspace-overview', component: { template: '<div />' } },
      { path: '/:rest(.*)*', component: { template: '<div />' } },
    ],
  })
  await router.push('/')
  await router.isReady()
})

/** 只装画法、语言包和路由 —— 没有 pinia，也没有任何 store / 接口。 */
function mount(over: Record<string, unknown> = {}) {
  return render(View, { props: props(over), global: { plugins: [vuetify, router] } })
}

describe('待办页「等你处理」这半张画面', () => {
  it('一个项目都没有时摆三条起步路，各自把动作发上去', async () => {
    const { emitted } = mount({ noProjects: true })

    expect(screen.getByText(t('work.startPaths.invite.title'))).toBeTruthy()
    expect(screen.getByText(t('work.startPaths.teams.title'))).toBeTruthy()
    expect(screen.getByText(t('work.startPaths.settingsNote'))).toBeTruthy()

    await fireEvent.click(screen.getByRole('button', { name: t('navigation.newProject', { project: '项目' }) }))
    expect(emitted()['new-project']).toHaveLength(1)

    await fireEvent.click(screen.getByRole('button', { name: t('work.startPaths.invite.action') }))
    expect(emitted().join).toHaveLength(1)
  })

  it('正在读 / 没读出来 / 暂时没有，三种状态各自说自己的话', async () => {
    const loading = mount({ loading: true })
    expect(loading.container.querySelector('.inbox__quiet .v-progress-circular')).toBeTruthy()
    loading.unmount()

    const failed = mount({ failed: true, errorReason: '服务器错误' })
    expect(screen.getByText(t('home.inbox.loadFailed'))).toBeTruthy()
    expect(screen.getByText('服务器错误')).toBeTruthy()
    failed.unmount()

    mount()
    expect(screen.getByText(t('home.inbox.waitingEmpty'))).toBeTruthy()
  })

  it('读失败时点重试把这一步发上去', async () => {
    const { emitted } = mount({ failed: true, errorReason: '服务器错误' })

    await fireEvent.click(screen.getByRole('button', { name: t('global.loadError.retry') }))

    expect(emitted().reload).toHaveLength(1)
  })

  it('按项目分组，每一件打开它自己那一条去处', async () => {
    mount({
      items: [
        item({ taskId: 't1', taskTitle: '分享页加访问口令', blockId: 'b-3' }),
        item({ reason: 'asked', phrase: 'awaiting_answer', threadId: 'th-1', detail: '口令要不要只发一次？' }),
        item({ reason: 'read', phrase: 'change_alert', alertId: 8, headline: '报名表单改了' }),
        item({
          projectId: 'p2',
          projectName: '编译器实验',
          topicId: null,
          topicTitle: '',
          reason: 'read',
          phrase: 'change_alert',
          alertId: 9,
          headline: '依赖升级了',
        }),
      ],
    })

    // 两件分属两个项目 → 两组，各写自己的项目名。
    expect(screen.getByText('课程项目')).toBeTruthy()
    expect(screen.getByText('编译器实验')).toBeTruthy()

    // 任务的事 → 任务页，并带上停在哪一块；不指向频道的那一条 → 项目总览。
    expect(screen.getByText('分享页加访问口令').closest('a')?.getAttribute('href')).toBe(
      '/p/p1/t/c1/tasks/t1?block=b-3'
    )
    expect(screen.getByText('口令要不要只发一次？').closest('a')?.getAttribute('href')).toBe('/p/p1/c/c1/threads/th-1')
    expect(screen.getByText('报名表单改了').closest('a')?.getAttribute('href')).toBe('/p/p1/t/c1')
    expect(screen.getByText('依赖升级了').closest('a')?.getAttribute('href')).toBe('/p/p2/overview')
  })

  it('要你拍板的：点哪个选项就把哪一句发上去', async () => {
    const decide = item({
      reason: 'decide',
      phrase: 'decision',
      alertId: 7,
      headline: '上线前要不要先灰度',
      options: ['先灰度', '直接上线'],
    })
    const { emitted } = mount({ items: [decide] })

    await fireEvent.click(screen.getByRole('button', { name: '直接上线' }))

    expect(emitted().decide).toHaveLength(1)
    expect(emitted().decide[0]).toEqual([decide, '直接上线'])
  })

  it('没带选项的决策请求：写一句答复才发，空白不发', async () => {
    const free = item({
      reason: 'decide',
      phrase: 'decision',
      alertId: 11,
      headline: '报名表单要不要加验证码',
      options: [],
    })
    const { emitted } = mount({ items: [free] })

    const answer = screen.getByRole('button', { name: t('home.inbox.answer') })
    await fireEvent.click(answer)
    expect(emitted().answer).toBeUndefined()

    // 只写空格也不算数：这一句是给后端的话，不是一次「我知道了」。
    await fireEvent.update(screen.getByRole('textbox', { name: t('home.inbox.answerLabel') }), '  ')
    await fireEvent.click(answer)
    expect(emitted().answer).toBeUndefined()

    await fireEvent.update(screen.getByRole('textbox', { name: t('home.inbox.answerLabel') }), ' 先不强制 ')
    await fireEvent.click(answer)
    expect(emitted().answer).toHaveLength(1)
    expect(emitted().answer[0]).toEqual([free, '先不强制'])
  })

  it('标为已读发一件，整个项目清发的是那个项目的 id', async () => {
    const { emitted } = mount({
      items: [
        item({ reason: 'read', phrase: 'change_alert', alertId: 8, headline: '报名表单改了' }),
        item({ reason: 'read', phrase: 'change_alert', alertId: 9, headline: '依赖升级了' }),
      ],
    })

    const markAll = screen.getByRole('button', { name: t('home.inbox.markAllRead') })
    await fireEvent.click(markAll)
    // 清的是这个项目，不是「全部」那一档动态（那是另一件事，见 `NotificationFeed`）。
    expect(emitted()['dismiss-all']).toEqual([['p1']])

    await fireEvent.click(screen.getAllByRole('button', { name: t('home.inbox.markRead') })[0])
    expect(emitted().dismiss).toHaveLength(1)
    expect((emitted().dismiss[0][0] as WaitingItem).alertId).toBe(8)
  })

  it('交不上去时把原因摆出来，这一件还在', async () => {
    mount({
      items: [item({ reason: 'read', phrase: 'change_alert', alertId: 8, headline: '报名表单改了' })],
      actionError: '无法提交',
    })

    expect(screen.getByRole('alert')).toBeTruthy()
    expect(screen.getByText('无法提交')).toBeTruthy()
    expect(screen.getByText('报名表单改了')).toBeTruthy()
  })

  it('只装画法、语言包和路由就挂得起来（不接任何 store / 接口）', () => {
    const { container } = mount({ items: [item({ taskId: 't1', taskTitle: '分享页加访问口令' })] })

    expect(container.innerHTML.trim()).not.toBe('')
    expect(screen.getByText(t('home.inbox.waiting'))).toBeTruthy()
  })
})
