// 话题头这一行常驻的只有标题、状态、成员；算力和专注模式收在 ⋯ 里。
//
// 收进去的东西里有一样不能跟着藏：房间能看到整台机器。那是权限，不是设置——
// 菜单合着的时候它也得在这一行上。
import type { Component } from 'vue'
import type { Topic, TopicComputeProfile } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getTopicComputeProfile = vi.fn()

vi.mock('@/api', () => ({
  getTopicComputeProfile: (...args: unknown[]) => getTopicComputeProfile(...args),
  setTopicComputeChoice: vi.fn(),
  getTopicUsage: vi.fn(async () => null),
  getProjectUsage: vi.fn(async () => null),
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
  name: '云端 · 标准配置',
  profile: 'cloud',
  device_id: null,
  cores: null,
  memory_mb: null,
  disk_gb: null,
}

function profile(machineAccess: boolean): TopicComputeProfile {
  return {
    choice: cloud,
    project_default: cloud,
    favorites: [],
    current: 'cloud',
    device_id: null,
    devices: [],
    locked: true,
    inherited: false,
    profiles: [],
    visibility: {
      options: [],
      effective: 'host',
      machine_access: machineAccess,
      notice: '它能读写这台机器上的所有文件',
    },
  } as TopicComputeProfile
}

function mountHeader(focus = false) {
  return render(Header, {
    props: { topic, members: [], me: 'me', connected: true, focus },
    global: {
      plugins: [createVuetify({ components, directives })],
      stubs: { TopicMembers: true },
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
  it('房间能看到整台机器时，菜单合着这一行上也写着', async () => {
    getTopicComputeProfile.mockResolvedValue(profile(true))
    mountHeader()

    await waitFor(() => expect(bar().textContent).toContain('整台机器'))
    expect(bar().querySelector('[title="它能读写这台机器上的所有文件"]')).toBeTruthy()
  })

  it('看不到整台机器时这一行不提它', async () => {
    getTopicComputeProfile.mockResolvedValue(profile(false))
    mountHeader()

    await waitFor(() => expect(getTopicComputeProfile).toHaveBeenCalled())
    await new Promise((r) => setTimeout(r, 0))
    expect(bar().textContent).not.toContain('整台机器')
  })

  it('运行环境不在这一行上，在 ⋯ 里', async () => {
    getTopicComputeProfile.mockResolvedValue(profile(false))
    mountHeader()

    await waitFor(() => expect(screen.getByText('运行环境')).toBeTruthy())
    expect(bar().textContent).not.toContain('云端 · 标准配置')
    expect(bar().contains(screen.getByText('运行环境'))).toBe(false)
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
