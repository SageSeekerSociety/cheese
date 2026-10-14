// 一条小队链接把人送到这一页时，页面上要用的是地址里那一段令牌。这一段走的是真路由记录
// （`props: true`），不是这一页自己去读 `route.params`；下面这份用例从地址出发，页面上
// 交给接口的那个令牌就是地址里那一个 —— 记录上那行 `props: true` 丢了，或参数改了名，
// 它拿到的就是 `undefined`。
import { RouterView } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, screen } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const detail = vi.fn()
const join = vi.fn()
vi.mock('@/api/teams', () => ({
  TeamsApi: {
    detailByJoinLink: (...args: unknown[]) => detail(...args),
    joinByJoinLink: (...args: unknown[]) => join(...args),
  },
}))
// 没登录时这一页不会去问后端，而这条用例要看的正是那一次请求。
vi.mock('@/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api')>()),
  authToken: () => 'signed-in',
}))

import i18n, { setLocale } from '@/i18n'
import router from '@/router'

beforeEach(() => {
  setLocale('zh-CN')
  detail.mockReset().mockResolvedValue({
    team: { id: 7, name: 'Cheese 核心组', members: { total: 3 }, joinApproval: true, joinStatus: 'none' },
  })
  join.mockReset()
})
afterEach(cleanup)

describe('the link in the address bar', () => {
  it('asks about the team named by the token in the address', async () => {
    render(RouterView, {
      global: { plugins: [createPinia(), router, createVuetify({ components, directives }), i18n] },
    })

    await router.push('/team-invites/some-token').catch(() => {})

    await screen.findByText('Cheese 核心组', undefined, { timeout: 20_000 })
    expect(detail).toHaveBeenCalledWith('some-token')
  }, 30_000)
})
