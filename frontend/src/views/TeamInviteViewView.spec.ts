/**
 * 小队链接落地页那一屏拆出来的画法（`TeamInviteViewView.vue`）：props 进、事件出。
 *
 * 它只有四条路，各钉一条：
 * 1. **小队拿到了**就画小队那一面，不再画落地卡片 —— 两样同时出现会让人以为还得再点一下。
 * 2. **没登录**时给的是登录入口，报的是 `sign-in`，页面自己去记这条链接。
 * 3. **读失败**时给一颗重试，报的是 `load`。
 * 4. **链接失效**（`invalid`）时说的是失效，**不给**那颗按不动的重试 —— 令牌重置过，
 *    再点多少次都是同一个 404。
 */
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import TeamInviteViewView from './TeamInviteViewView.vue'

import { setLocale } from '@/i18n'
import { teamProfile } from '@/views/demo/catalogSharedFixtures'

const Blank = { template: '<div />' }
const router = () =>
  createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: Blank },
      { path: '/teams/:handle', name: 'TeamsDetailDefault', component: Blank },
      { path: '/:pathMatch(.*)*', component: Blank },
    ],
  })

type ViewProps = InstanceType<typeof TeamInviteViewView>['$props']
const base: ViewProps = {
  team: null,
  join: async () => {},
  needsLogin: false,
  error: '',
  busy: false,
  invalid: false,
}

function mount(props: Partial<ViewProps> = {}) {
  return render(TeamInviteViewView, {
    props: { ...base, ...props },
    global: { plugins: [createVuetify({ components, directives }), router()] },
  })
}

beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

describe('打开一条小队链接', () => {
  it('小队拿到了就画小队那一面，不再画落地卡片', async () => {
    mount({ team: teamProfile() })
    await screen.findByText('知是')
    expect(screen.queryByText('登录后可查看团队信息，再决定是否加入')).toBeNull()
  })

  it('没登录就给登录入口，报的是 sign-in', async () => {
    const view = mount({ needsLogin: true })
    await fireEvent.click(screen.getByRole('button', { name: '登录并继续' }))
    expect((view.emitted()['sign-in'] as unknown[][]).length).toBe(1)
  })

  it('读失败给一颗重试，报的是 load', async () => {
    const view = mount({ error: '操作失败，请重试' })
    screen.getByText('操作失败，请重试')
    await fireEvent.click(screen.getByRole('button', { name: '重试' }))
    expect((view.emitted()['load'] as unknown[][]).length).toBe(1)
  })

  it('链接失效时不给那颗按不动的重试', () => {
    mount({ error: '团队链接已失效，请向团队管理员索取新链接', invalid: true })
    screen.getByText('团队链接已失效，请向团队管理员索取新链接')
    expect(screen.queryByRole('button', { name: '重试' })).toBeNull()
  })

  it('在等结果时画的是进度条，不是重试', () => {
    mount({ busy: true, error: '操作失败，请重试' })
    screen.getByLabelText('加载中')
    expect(screen.queryByRole('button', { name: '重试' })).toBeNull()
  })
})
