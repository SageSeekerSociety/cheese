// @vitest-environment jsdom
//
// 手机上话题里 Back 的规矩：不在对话那一格时，Back 先回到对话，再 Back 才离开话题；
// 顶栏的 ← 也一样。桌面上换页签不进历史，Back 直接离开话题。
//
// 用 jsdom 是因为要一套真的浏览器历史（back / popstate / state），happy-dom 的
// History 是空壳。
import type { Ref } from 'vue'

import { defineComponent, h, ref } from 'vue'
import { createRouter, createWebHistory, RouterView } from 'vue-router'
import { render } from '@testing-library/vue'
import { afterEach, describe, expect, it } from 'vitest'

import { useRoomTabHistory } from './useRoomTabHistory'

let api: ReturnType<typeof useRoomTabHistory>

function makeRoom(phone: Ref<boolean>) {
  return defineComponent({
    setup() {
      api = useRoomTabHistory(phone)
      void api.ensureChatBehind()
      return () => h('div')
    },
  })
}

async function setup(opts: { phone: boolean; start: string }) {
  window.history.replaceState(null, '', '/')
  const phone = ref(opts.phone)
  const router = createRouter({
    history: createWebHistory(),
    routes: [
      { path: '/', component: { render: () => h('div') } },
      { path: '/list', name: 'list', component: { render: () => h('div') } },
      { path: '/room', name: 'room', component: makeRoom(phone) },
    ],
  })
  await router.push('/list')
  await router.push(opts.start)
  render({ render: () => h(RouterView) }, { global: { plugins: [router] } })
  await settle()
  return router
}

async function settle() {
  for (let i = 0; i < 5; i++) await new Promise((r) => setTimeout(r, 5))
}

async function browserBack() {
  window.history.back()
  await settle()
}

function where(router: ReturnType<typeof createRouter>) {
  const r = router.currentRoute.value
  if (r.name !== 'room') return String(r.name)
  const tab = r.query.tab
  return `room:${typeof tab === 'string' ? tab : 'chat'}`
}

afterEach(() => window.history.replaceState(null, '', '/'))

describe('手机上话题里的 Back', () => {
  it('从别的页签 Back 先回到对话，再 Back 离开话题', async () => {
    const router = await setup({ phone: true, start: '/room' })
    api.goTab('changes')
    await settle()
    api.goTab('preview')
    await settle()
    expect(where(router)).toBe('room:preview')

    await browserBack()
    expect(where(router)).toBe('room:chat')
    await browserBack()
    expect(where(router)).toBe('list')
  })

  it('顶栏的 ← 从别的页签回到对话，之后 Back 离开话题', async () => {
    const router = await setup({ phone: true, start: '/room' })
    api.goTab('changes')
    await settle()
    api.toChat()
    await settle()
    expect(where(router)).toBe('room:chat')
    await browserBack()
    expect(where(router)).toBe('list')
  })

  it('在对话和别的页签之间来回切，Back 仍然一下离开话题', async () => {
    const router = await setup({ phone: true, start: '/room' })
    for (const key of ['changes', 'chat', 'site', 'chat', 'overview', 'chat']) {
      api.goTab(key)
      await settle()
    }
    expect(where(router)).toBe('room:chat')
    await browserBack()
    expect(where(router)).toBe('list')
  })

  it('从链接直接打开改动页签，Back 先回到对话', async () => {
    const router = await setup({ phone: true, start: '/room?tab=changes' })
    expect(where(router)).toBe('room:changes')
    await browserBack()
    expect(where(router)).toBe('room:chat')
    await browserBack()
    expect(where(router)).toBe('list')
  })
})

describe('桌面上话题里的 Back', () => {
  it('换页签不进历史，Back 离开话题', async () => {
    const router = await setup({ phone: false, start: '/room?tab=overview' })
    api.goTab('changes')
    await settle()
    api.goTab('preview')
    await settle()
    await browserBack()
    expect(where(router)).toBe('list')
  })
})
