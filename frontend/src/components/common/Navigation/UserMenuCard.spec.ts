/**
 * 「我」的菜单（`UserMenuCard`）。桌面左栏与手机顶栏共用这一份，此前两处各写一遍，
 * 手机那份就漏了「我的设备」—— 这里钉住菜单里每个人都该找得到的几样。
 */
import { computed, ref } from 'vue'
import { createRouter, createWebHashHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import UserMenuCard from './UserMenuCard.vue'

import i18n, { setLocale } from '@/i18n'

function fakeMenu(intro: string) {
  return {
    menuOpen: ref(true),
    loggedIn: computed(() => true),
    currentUser: computed(() => ({ id: 42, username: 'alice' })),
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
      { path: '/users/:handle', name: 'UserPage', component: stub },
      { path: '/devices', name: 'my-devices', component: stub },
      { path: '/connections', name: 'my-connections', component: stub },
      { path: '/users/settings/profile', name: 'UserSettingsProfile', component: stub },
    ],
  })
  await router.push('/')
  await router.isReady()
  const vuetify = createVuetify({ components, directives })
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return render(UserMenuCard, { props: { menu: fakeMenu(intro) as any }, global: { plugins: [vuetify, router] } })
}

describe('「我」的菜单', () => {
  // 初始语言跟着浏览器（happy-dom 报 en-US），断言写的是中文，所以每条先定成中文。
  beforeEach(() => setLocale('zh-CN'))

  it('个人主页、个人设置、我的设备都在，并且能退出登录', async () => {
    const view = await mount()
    // 主页按 handle 找人，和 @提及、成员名册同一种地址。
    expect(view.getByText('个人主页').closest('a')?.getAttribute('href')).toBe('#/users/alice')
    // 上传头像的地方：以前菜单里没有这一项，得先进个人主页再点资料卡上的编辑。
    expect(view.getByText('个人设置').closest('a')?.getAttribute('href')).toBe('#/users/settings/profile')
    expect(view.getByText('我的设备').closest('a')?.getAttribute('href')).toBe('#/devices')
    expect(view.getByText('退出登录')).toBeTruthy()
  })

  it('在菜单里切换界面语言', async () => {
    const view = await mount()
    await fireEvent.click(view.getByRole('radio', { name: 'English' }))
    expect(i18n.global.locale.value).toBe('en')
    expect(await view.findByText('Log out')).toBeTruthy()
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
