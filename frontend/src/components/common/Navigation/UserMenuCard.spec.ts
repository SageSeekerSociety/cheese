/**
 * 「我」的菜单（`UserMenuCard`）。桌面左栏与手机顶栏共用这一份，此前两处各写一遍，
 * 手机那份就漏了「我的设备」—— 这里钉住菜单里每个人都该找得到的几样。
 */
import { computed, ref } from 'vue'
import { createRouter, createWebHashHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'

import UserMenuCard from './UserMenuCard.vue'

function fakeMenu(intro: string) {
  return {
    menuOpen: ref(true),
    loggedIn: computed(() => true),
    currentUser: computed(() => ({ id: 42 })),
    avatar: computed(() => null),
    avatarInitial: computed(() => '爱'),
    avatarColor: computed(() => '#6a5acd'),
    nickname: computed(() => '爱丽丝'),
    intro: computed(() => intro),
    onLogout: vi.fn(),
  }
}

async function mount(intro = '') {
  const stub = { template: '<div />' }
  const router = createRouter({
    history: createWebHashHistory(),
    routes: [
      { path: '/', component: stub },
      { path: '/users/:id', name: 'UserDefault', component: stub },
      { path: '/devices', name: 'my-devices', component: stub },
      { path: '/about', component: stub },
    ],
  })
  await router.push('/')
  await router.isReady()
  const vuetify = createVuetify({ components, directives })
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return render(UserMenuCard, { props: { menu: fakeMenu(intro) as any }, global: { plugins: [vuetify, router] } })
}

describe('「我」的菜单', () => {
  it('个人中心、我的设备、了解知是都在，并且能退出登录', async () => {
    const view = await mount()
    expect(view.getByText('个人中心').closest('a')?.getAttribute('href')).toBe('#/users/42')
    expect(view.getByText('我的设备').closest('a')?.getAttribute('href')).toBe('#/devices')
    expect(view.getByText('了解知是').closest('a')?.getAttribute('href')).toBe('#/about')
    expect(view.getByText('退出登录')).toBeTruthy()
  })

  it('没写简介时说「暂无个人简介」', async () => {
    expect((await mount()).getByText('暂无个人简介')).toBeTruthy()
  })

  it('写了简介就显示简介', async () => {
    const view = await mount('研究生，做推荐系统')
    expect(view.getByText('研究生，做推荐系统')).toBeTruthy()
    expect(view.queryByText('暂无个人简介')).toBeNull()
  })

  it('显示名字和 UID', async () => {
    const view = await mount()
    expect(view.getByText('爱丽丝')).toBeTruthy()
    expect(view.getByText('UID 42')).toBeTruthy()
  })
})
