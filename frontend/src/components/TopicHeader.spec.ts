// 话题头这一行常驻的只有标题、状态、成员；专注模式收在 ⋯ 里，工作电脑写在成员名册里。
//
// 名册里的东西有一样不能跟着藏：有 AI 队友能访问整台机器。那是权限，不是设置——
// 名册合着的时候它也得在这一行上。
import type { Component } from 'vue'
import type { Topic } from '@/cx_types'
import type { TopicComputeProfile } from '@/types/compute'

import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getTopicComputeProfile = vi.fn()
const archiveTopic = vi.fn(async (id: string) => ({ id, status: 'archived' }))
const unarchiveTopic = vi.fn(async (id: string) => ({ id, status: 'active' }))

vi.mock('@/api', () => ({
  getTopicComputeProfile: (...args: unknown[]) => getTopicComputeProfile(...args),
  listTopicMembers: vi.fn(async () => ({ data: [], total: 0 })),
  setTopicComputeChoice: vi.fn(),
  getTopicUsage: vi.fn(async () => null),
  getProjectUsage: vi.fn(async () => null),
  archiveTopic: (...args: [string]) => archiveTopic(...args),
  unarchiveTopic: (...args: [string]) => unarchiveTopic(...args),
}))

import TopicHeader from './TopicHeader.vue'

import { setLocale } from '@/i18n'

const Header = TopicHeader as unknown as Component

const topic = {
  id: 'topic-1',
  project_id: 'p1',
  parent_id: null,
  title: '登录页改成深色',
  kind: 'topic',
  status: 'active',
  created_by: 'u',
  created_at: '2026-09-01T00:00:00Z',
  updated_at: '2026-09-01T00:00:00Z',
} as Topic

const cloud = {
  name: null,
  profile: 'cloud',
  device_id: null,
}

function profile(machineAccess: boolean): TopicComputeProfile {
  return {
    choice: cloud,
    project_default: cloud,
    current: 'cloud',
    device_id: null,
    devices: [],
    sessions: [],
    profiles: [],
    cloud_vm_available: false,
    visibility: {
      options: [],
      effective: 'host',
      machine_access: machineAccess,
    },
  } as TopicComputeProfile
}

function mountHeader(focus = false, over: Partial<Topic> = {}) {
  return render(Header, {
    props: { topic: { ...topic, ...over }, members: [], me: 'me', connected: true, focus },
    global: {
      plugins: [
        createVuetify({ components, directives }),
        createPinia(),
        createRouter({
          history: createMemoryHistory(),
          routes: [{ path: '/:any(.*)*', component: { render: () => null } }],
        }),
      ],
    },
  })
}

/** 头这一行本身（不含浮层）。菜单的内容渲染在 body 下的浮层容器里。 */
function bar(): HTMLElement {
  return document.querySelector('.topic-header') as HTMLElement
}

beforeAll(() => {
  // 桌面宽度：专注模式只在有第二栏可以让开的时候存在。
  Object.defineProperty(window, 'innerWidth', { value: 1280, writable: true, configurable: true })
  // happy-dom 没有这两样，而菜单打开时 Vuetify 要读它们来摆位置。
  if (!('devicePixelRatio' in globalThis)) {
    Object.defineProperty(globalThis, 'devicePixelRatio', { configurable: true, value: 1 })
  }
  if (!globalThis.visualViewport) {
    Object.defineProperty(globalThis, 'visualViewport', {
      configurable: true,
      value: { width: 1280, height: 800, offsetLeft: 0, offsetTop: 0, addEventListener() {}, removeEventListener() {} },
    })
  }
})

beforeEach(() => {
  setLocale('zh-CN')
  getTopicComputeProfile.mockReset()
})

afterEach(cleanup)

describe('话题头', () => {
  it('有 AI 队友能访问整台机器时，名册合着这一行上也写着', async () => {
    getTopicComputeProfile.mockResolvedValue(profile(true))
    mountHeader()

    await waitFor(() => expect(bar().textContent).toContain('能访问整台机器'))
    expect(bar().querySelector('[title="让它看到整台机器（能操作这台机器上的服务和其他房间）"]')).toBeTruthy()
  })

  it('看不到能访问整台机器时这一行不提它', async () => {
    getTopicComputeProfile.mockResolvedValue(profile(false))
    mountHeader()

    await waitFor(() => expect(getTopicComputeProfile).toHaveBeenCalled())
    await new Promise((r) => setTimeout(r, 0))
    expect(bar().textContent).not.toContain('能访问整台机器')
  })

  it('⋯ 里没有工作电脑，它在成员名册里', async () => {
    getTopicComputeProfile.mockResolvedValue(profile(false))
    mountHeader()

    await fireEvent.click(screen.getByRole('button', { name: '更多' }))
    await screen.findByRole('button', { name: '专注模式' })
    expect(document.body.textContent).not.toContain('工作电脑')
    expect(bar().textContent).not.toContain('云端沙箱')
  })

  it('专注模式从 ⋯ 里进', async () => {
    getTopicComputeProfile.mockResolvedValue(profile(false))
    const { emitted } = mountHeader(false)

    expect(bar().querySelector('[aria-label="退出专注模式"]')).toBeNull()
    await fireEvent.click(screen.getByRole('button', { name: '更多' }))
    await fireEvent.click(await screen.findByRole('button', { name: '专注模式' }))

    expect(emitted()['toggle-focus']).toHaveLength(1)
  })

  it('在专注模式里，出口摆在这一行上', async () => {
    getTopicComputeProfile.mockResolvedValue(profile(false))
    const { emitted } = mountHeader(true)

    await fireEvent.click(bar().querySelector('[aria-label="退出专注模式"]') as HTMLElement)

    expect(emitted()['toggle-focus']).toHaveLength(1)
  })
})

// 手机上话题列表的行尾没有 ⋯，改名、归档在房间顶栏的 ⋯ 里也得找得到。
describe('手机上话题头的 ⋯', () => {
  let slot: HTMLElement

  beforeEach(() => {
    window.innerWidth = 390
    getTopicComputeProfile.mockResolvedValue(profile(false))
    // 手机上这一行画进外壳顶栏里的那一格。
    slot = document.createElement('div')
    slot.id = 'app-bar-slot'
    document.body.appendChild(slot)
  })

  afterEach(() => {
    window.innerWidth = 1280
    slot.remove()
  })

  async function openMore() {
    await fireEvent.click(await screen.findByRole('button', { name: '更多' }))
  }

  it('改名：填上新名字保存，改的就是这个话题', async () => {
    const { emitted } = mountHeader(false, { can_archive: true })

    await openMore()
    await fireEvent.click(await screen.findByRole('menuitem', { name: '重命名' }))
    const field = await screen.findByLabelText('话题名称')
    await fireEvent.update(field, '登录页改成浅色')
    await fireEvent.click(screen.getByRole('button', { name: '保存' }))

    expect(emitted().rename).toEqual([['登录页改成浅色']])
  })

  it('名字没改就保存，不算改名', async () => {
    const { emitted } = mountHeader(false, { can_archive: true })

    await openMore()
    await fireEvent.click(await screen.findByRole('menuitem', { name: '重命名' }))
    await screen.findByLabelText('话题名称')
    await fireEvent.click(screen.getByRole('button', { name: '保存' }))

    expect(emitted().rename).toBeUndefined()
  })

  it('能归档的人可以从这里归档', async () => {
    mountHeader(false, { can_archive: true })

    await openMore()
    await fireEvent.click(await screen.findByRole('menuitem', { name: '归档' }))

    expect(archiveTopic).toHaveBeenCalledWith('topic-1')
  })

  it('不能归档的人看不到归档', async () => {
    mountHeader(false, { can_archive: false })

    await openMore()
    await screen.findByRole('menuitem', { name: '重命名' })
    expect(screen.queryByRole('menuitem', { name: '归档' })).toBeNull()
  })

  it('已归档的话题只能取消归档', async () => {
    mountHeader(false, { status: 'archived', can_archive: true })

    await openMore()
    await fireEvent.click(await screen.findByRole('menuitem', { name: '取消归档' }))

    expect(unarchiveTopic).toHaveBeenCalledWith('topic-1')
    expect(screen.queryByRole('menuitem', { name: '重命名' })).toBeNull()
  })
})
