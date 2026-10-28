// 话题头这一行常驻的只有标题、状态、成员；专注模式收在 ⋯ 里，工作电脑写在成员名册里。
//
// 名册里的东西有一样不能跟着藏：有 AI 队友能访问整台电脑。那是权限，不是设置——
// 名册合着的时候它也得在这一行上。
import type { Component } from 'vue'
import type { Topic } from '@/cx_types'
import type { TopicEnvironment } from '@/types/compute'

import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getTopicEnvironment = vi.fn()
const getProject = vi.fn()
const archiveTopic = vi.fn(async (id: string) => ({ id, status: 'archived' }))
const unarchiveTopic = vi.fn(async (id: string) => ({ id, status: 'active' }))

vi.mock('@/api', () => ({
  getTopicEnvironment: (...args: unknown[]) => getTopicEnvironment(...args),
  getProject: (...args: unknown[]) => getProject(...args),
  listTopicMembers: vi.fn(async () => ({ data: [], total: 0 })),
  getTopicUsage: vi.fn(async () => null),
  getProjectUsage: vi.fn(async () => null),
  archiveTopic: (...args: [string]) => archiveTopic(...args),
  unarchiveTopic: (...args: [string]) => unarchiveTopic(...args),
}))

import TopicHeader from './TopicHeader.vue'

import { setLocale, t } from '@/i18n'

const Header = TopicHeader as unknown as Component

const topic = {
  id: 'topic-1',
  project_id: 'p1',
  parent_id: null,
  title: '登录页改成深色',
  kind: 'channel',
  status: 'active',
  created_by: 'u',
  created_at: '2026-09-01T00:00:00Z',
  updated_at: '2026-09-01T00:00:00Z',
} as Topic

function profile(): TopicEnvironment {
  return {
    default: { kind: 'sandbox', spec: 'standard' },
    default_from: 'project',
    devices: [],
    specs: [{ spec: 'standard', label: '标准' }],
    sessions: [],
  }
}

// 专注模式只在面板和对话并排开着时成立；默认按并排开着来挂。
function mountHeader(over: Partial<Topic> = {}) {
  return render(Header, {
    props: { topic: { ...topic, ...over }, members: [], me: 'me', connected: true },
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
  getTopicEnvironment.mockReset()
})

afterEach(cleanup)

describe('话题头', () => {
  it('⋯ 里没有环境，它在成员名册里', async () => {
    getTopicEnvironment.mockResolvedValue(profile())
    mountHeader()

    await fireEvent.click(screen.getByRole('button', { name: '更多' }))
    await waitFor(() => expect(document.querySelector('.room-menu')).toBeTruthy())
    expect(document.body.textContent).not.toContain('新 AI 队友')
    expect(bar().textContent).not.toContain('沙箱')
  })
})

// 桌面上这颗 ⋯ 原先只开用量：改名、归档在桌面版根本没有别的入口（清单第 1、3 条）。
// 现在它和手机上问的是同一份话题操作，用量退成菜单顶上的一段。
describe('桌面上话题头的 ⋯', () => {
  async function openMore() {
    await fireEvent.click(screen.getByRole('button', { name: '更多' }))
    await screen.findByRole('menuitem', { name: '复制链接' })
  }

  it('改名：填上新名字保存，改的就是这个话题', async () => {
    const { emitted } = mountHeader({ can_manage: true })

    await openMore()
    await fireEvent.click(await screen.findByRole('menuitem', { name: '重命名' }))
    const field = await screen.findByLabelText('频道名称')
    await fireEvent.update(field, '登录页改成浅色')
    await fireEvent.click(screen.getByRole('button', { name: '保存' }))

    expect(emitted().rename).toEqual([['登录页改成浅色']])
  })

  it('能归档的人可以从这里归档', async () => {
    mountHeader({ can_manage: true })

    await openMore()
    await fireEvent.click(await screen.findByRole('menuitem', { name: '归档' }))

    expect(archiveTopic).toHaveBeenCalledWith('topic-1')
  })

  it('频道里还有进行中的任务时不显示归档', async () => {
    mountHeader({ can_manage: true, my_tasks: { shown: [], open: 1 } })

    await openMore()
    expect(screen.queryByRole('menuitem', { name: '归档' })).toBeNull()
  })

  it('用量还在菜单里：它是这段的口径，不是一件能做的事', async () => {
    mountHeader()

    await openMore()
    expect(document.body.textContent).toContain('用量')
  })
})

// 删除不可撤销，所以按不动的那一下要有人守着：频道名没打对，删除键就是灰的。
describe('删除频道', () => {
  async function openDelete() {
    await fireEvent.click(screen.getByRole('button', { name: '更多' }))
    await fireEvent.click(await screen.findByRole('menuitem', { name: '删除频道' }))
    await screen.findByLabelText('频道名称')
  }

  function confirmButton(): HTMLButtonElement {
    return screen.getByRole('button', { name: '删除频道' }) as HTMLButtonElement
  }

  it('把频道名原样打一遍才点得动', async () => {
    mountHeader({ can_manage: true })

    await openDelete()
    expect(confirmButton().disabled).toBe(true)

    await fireEvent.update(screen.getByLabelText('频道名称'), '登录页改成深色')
    expect(confirmButton().disabled).toBe(false)
  })

  it('不管这个频道的人没有这一项', async () => {
    mountHeader({ can_manage: false })

    await fireEvent.click(screen.getByRole('button', { name: '更多' }))
    await screen.findByRole('menuitem', { name: '复制链接' })
    expect(screen.queryByRole('menuitem', { name: '删除频道' })).toBeNull()
  })

  it('「综合」是项目本身，删不得', async () => {
    mountHeader({ kind: 'root', can_manage: true })

    await fireEvent.click(screen.getByRole('button', { name: '更多' }))
    await screen.findByRole('menuitem', { name: '复制链接' })
    expect(screen.queryByRole('menuitem', { name: '删除频道' })).toBeNull()
  })
})

// 手机上话题列表的行尾没有 ⋯，改名、归档在房间顶栏的 ⋯ 里也得找得到。
describe('手机上话题头的 ⋯', () => {
  beforeEach(() => {
    window.innerWidth = 390
    getTopicEnvironment.mockResolvedValue(profile())
  })

  afterEach(() => {
    window.innerWidth = 1280
  })

  async function openMore() {
    await fireEvent.click(await screen.findByRole('button', { name: '更多' }))
  }

  it('改名：填上新名字保存，改的就是这个话题', async () => {
    const { emitted } = mountHeader({ can_manage: true })

    await openMore()
    await fireEvent.click(await screen.findByRole('menuitem', { name: '重命名' }))
    const field = await screen.findByLabelText('频道名称')
    await fireEvent.update(field, '登录页改成浅色')
    await fireEvent.click(screen.getByRole('button', { name: '保存' }))

    expect(emitted().rename).toEqual([['登录页改成浅色']])
  })

  it('名字没改就保存，不算改名', async () => {
    const { emitted } = mountHeader({ can_manage: true })

    await openMore()
    await fireEvent.click(await screen.findByRole('menuitem', { name: '重命名' }))
    await screen.findByLabelText('频道名称')
    await fireEvent.click(screen.getByRole('button', { name: '保存' }))

    expect(emitted().rename).toBeUndefined()
  })

  it('能归档的人可以从这里归档', async () => {
    mountHeader({ can_manage: true })

    await openMore()
    await fireEvent.click(await screen.findByRole('menuitem', { name: '归档' }))

    expect(archiveTopic).toHaveBeenCalledWith('topic-1')
  })

  it('不管这个频道的人能改名，但不能归档', async () => {
    mountHeader({ can_manage: false })

    await openMore()
    // 改名只要「进得来这个频道」；频道叫什么是用它的人一起定的事。
    await screen.findByRole('menuitem', { name: '重命名' })
    expect(screen.queryByRole('menuitem', { name: '归档' })).toBeNull()
  })

  it('已归档的频道，不管它的人一样动不了', async () => {
    mountHeader({ status: 'archived', can_manage: false })

    await openMore()
    await screen.findByRole('menuitem', { name: '复制链接' })
    expect(screen.queryByRole('menuitem', { name: '重命名' })).toBeNull()
    expect(screen.queryByRole('menuitem', { name: '取消归档' })).toBeNull()
  })

  it('已归档的话题只能取消归档', async () => {
    mountHeader({ status: 'archived', can_manage: true })

    await openMore()
    await fireEvent.click(await screen.findByRole('menuitem', { name: '取消归档' }))

    expect(unarchiveTopic).toHaveBeenCalledWith('topic-1')
    expect(screen.queryByRole('menuitem', { name: '重命名' })).toBeNull()
  })
})

describe('频道成员名单里的添加', () => {
  // 每个用例一个项目：项目那一份读进共享的查询缓存里，换个角色得换个项目。
  async function openRosterAs(projectId: string, permissions: string[]) {
    getTopicEnvironment.mockResolvedValue(profile())
    getProject.mockResolvedValue({ id: projectId, name: 'P', my_permissions: permissions })
    mountHeader({ project_id: projectId, joined: true })
    await fireEvent.click(bar().querySelector('.members-mini') as HTMLElement)
    await screen.findByPlaceholderText(t('work.room.roster.addPlaceholder')).catch(() => null)
  }

  it('角色允许拉人进频道的人，在自己所在的频道里能添加', async () => {
    await openRosterAs('p-member', ['speak', 'create_channels'])
    expect(await screen.findByRole('button', { name: t('work.room.roster.add') })).toBeTruthy()
  })

  it('角色不允许的人，人在频道里也添加不了', async () => {
    await openRosterAs('p-mentor', ['speak', 'ask_agent', 'comment'])
    await waitFor(() => expect(screen.queryByRole('button', { name: t('work.room.roster.add') })).toBeNull())
  })
})
