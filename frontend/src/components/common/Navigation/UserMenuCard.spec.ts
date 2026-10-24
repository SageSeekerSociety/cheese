/**
 * 「我」的菜单（`UserMenuCard`）。桌面左栏与手机顶栏共用这一份，此前两处各写一遍，
 * 手机那份就漏了一项 —— 这里钉住菜单里每个人都该找得到的几样。
 */
import { computed, ref } from 'vue'
import { createRouter, createWebHashHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useDesktopApp } from '@/composables/useDesktopApp'

import UserMenuCard from './UserMenuCard.vue'

import i18n, { setLocale } from '@/i18n'

function fakeMenu(seatsLeft: number) {
  return {
    menuOpen: ref(true),
    loggedIn: computed(() => true),
    currentUser: computed(() => ({ id: 42, username: 'alice' })),
    avatar: computed(() => null),
    avatarInitial: computed(() => '爱'),
    avatarColor: computed(() => '#6a5acd'),
    nickname: computed(() => '爱丽丝'),
    inviteSeatsLeft: ref(seatsLeft),
    onLogout: vi.fn(),
  }
}

async function mount(seatsLeft = 3) {
  const stub = { template: '<div />' }
  const router = createRouter({
    history: createWebHashHistory(),
    routes: [
      { path: '/', component: stub },
      { path: '/users/:handle', name: 'UserPage', component: stub },
      { path: '/download', name: 'Download', component: stub },
      { path: '/archived-projects', name: 'my-archived-projects', component: stub },
      { path: '/users/settings', name: 'UserSettings', component: stub },
      { path: '/users/settings/invites', name: 'UserSettingsInvites', component: stub },
    ],
  })
  await router.push('/')
  await router.isReady()
  const vuetify = createVuetify({ components, directives })
  return render(UserMenuCard, {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    props: { menu: fakeMenu(seatsLeft) as any },
    global: { plugins: [vuetify, router] },
  })
}

describe('「我」的菜单', () => {
  // 初始语言跟着浏览器（happy-dom 报 en-US），断言写的是中文，所以每条先定成中文。
  beforeEach(() => setLocale('zh-CN'))

  it('点头部的名字就是去自己的主页，头部写着 @handle', async () => {
    const view = await mount()
    // 主页按 handle 找人，和 @提及、成员名册同一种地址。
    const head = view.getByRole('link', { name: '个人主页' })
    expect(head.getAttribute('href')).toBe('#/users/alice')
    expect(head.textContent).toContain('爱丽丝')
    expect(head.textContent).toContain('@alice')
  })

  it('「邀请好友」右边写着自己的码还剩几个，点进去是邀请页', async () => {
    const view = await mount()
    const item = view.getByText('邀请好友').closest('a')
    expect(item?.getAttribute('href')).toBe('#/users/settings/invites')
    expect(item?.textContent).toContain('剩 3 个')
  })

  it('名额用完了右边什么也不写，照样能进邀请页', async () => {
    const view = await mount(0)
    const item = view.getByText('邀请好友').closest('a')
    expect(item?.getAttribute('href')).toBe('#/users/settings/invites')
    expect(view.queryByText(/剩 \d+ 个/)).toBeNull()
  })

  it('设置、已归档的项目、下载客户端都在，并且能退出登录', async () => {
    const view = await mount()
    // 上传头像的地方：以前菜单里没有这一项，得先进个人主页再点资料卡上的编辑。
    expect(view.getByText('设置').closest('a')?.getAttribute('href')).toBe('#/users/settings')
    expect(view.getByText('下载客户端').closest('a')?.getAttribute('href')).toBe('#/download')
    // 归档了的项目不在任何列表里，这里是所有者找回它们的地方。
    expect(view.getByText('已归档的项目').closest('a')?.getAttribute('href')).toBe('#/archived-projects')
    expect(view.getByText('退出登录')).toBeTruthy()
  })

  it('在菜单里切换界面语言', async () => {
    const view = await mount()
    await fireEvent.click(view.getByRole('radio', { name: 'English' }))
    expect(i18n.global.locale.value).toBe('en')
    expect(await view.findByText('Log out')).toBeTruthy()
  })
})

// 桌面 app 已经装在这台电脑上了：菜单里不再劝人下载，只留下还有意义的手机那一块。
describe('「我」的菜单，在桌面 app 里', () => {
  beforeEach(() => {
    setLocale('zh-CN')
    ;(window as unknown as { __TAURI__?: unknown }).__TAURI__ = { core: { invoke: vi.fn() } }
  })
  afterEach(() => {
    delete (window as unknown as { __TAURI__?: unknown }).__TAURI__
  })

  it('不给下载，给「在手机上使用」的扫码框', async () => {
    const view = await mount()
    expect(view.queryByText('下载客户端')).toBeNull()
    const { phoneOpen } = useDesktopApp()
    expect(phoneOpen.value).toBe(false)
    await fireEvent.click(view.getByText('在手机上使用'))
    expect(phoneOpen.value).toBe(true)
  })
})
