// 频道页头：桌面上点频道名、点那一串头像打开频道详情（关于 / 成员 / 设置），改名、归档、
// 删除、通知都在详情里；页头上没有管频道的 ⋯ 和铃铛。手机上页头是顶栏，频道的那一份
// 操作在顶栏的 ⋯ 里。
import type { Component } from 'vue'
import type { Topic } from '@/cx_types'
import type { TopicEnvironment } from '@/types/compute'

import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getTopicEnvironment = vi.fn()
const getProject = vi.fn()
const archiveTopic = vi.fn(async (id: string) => ({ id, status: 'archived' }))
const unarchiveTopic = vi.fn(async (id: string) => ({ id, status: 'active' }))
const setTopicNotifyLevel = vi.fn(async () => ({}))

vi.mock('@/api', () => ({
  getTopicEnvironment: (...args: unknown[]) => getTopicEnvironment(...args),
  getProject: (...args: unknown[]) => getProject(...args),
  listTopicMembers: vi.fn(async () => ({ data: [], total: 0 })),
  getTopicUsage: vi.fn(async () => null),
  getProjectUsage: vi.fn(async () => null),
  archiveTopic: (...args: [string]) => archiveTopic(...args),
  unarchiveTopic: (...args: [string]) => unarchiveTopic(...args),
  setTopicNotifyLevel: (...args: unknown[]) => setTopicNotifyLevel(...(args as [])),
  getTopicUnread: vi.fn(async () => ({})),
}))

import TopicHeader from './TopicHeader.vue'

import { requestChannelDetails } from '@/commands/channelDetails'
import { setLocale, t } from '@/i18n'
import { queryClient } from '@/query/client'
import { keys } from '@/query/keys'

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
  getTopicEnvironment.mockResolvedValue(profile())
  setTopicNotifyLevel.mockClear()
  queryClient.removeQueries({ queryKey: keys.projectNotifyLevels('') })
})

afterEach(cleanup)

describe('桌面上的频道页头', () => {
  it('没有管频道的 ⋯ 和铃铛', () => {
    mountHeader({ can_manage: true, joined: true })
    expect(screen.queryByRole('button', { name: '更多' })).toBeNull()
    expect(screen.queryByRole('button', { name: '通知' })).toBeNull()
  })

  it('静音时频道名旁边有个记号，说的是此刻那一档', async () => {
    queryClient.setQueryData(keys.projectNotifyLevels(''), { 'topic-1': { level: 'mute', muted_until: null } })
    mountHeader({ joined: true })
    expect((await screen.findByRole('img', { name: '静音' })).getAttribute('title')).toBe('静音')
  })

  it('没静音就没有那个记号', () => {
    mountHeader({ joined: true })
    expect(screen.queryByRole('img', { name: '静音' })).toBeNull()
  })
})

// 频道详情：点频道名打开，改名、归档、删除、通知、用量、编号都在里面。
describe('频道详情', () => {
  async function openDetails(tab?: string) {
    await fireEvent.click(screen.getByRole('button', { name: t('work.channelDetails.open', { name: topic.title }) }))
    if (tab) await fireEvent.click(await screen.findByRole('tab', { name: tab }))
  }

  it('「关于」里有编号、复制链接和用量', async () => {
    mountHeader()
    await openDetails()
    expect(await screen.findByText('#topic-')).toBeTruthy()
    expect(screen.getByRole('button', { name: '复制链接' })).toBeTruthy()
    expect(screen.getByText('用量')).toBeTruthy()
    expect(screen.getByRole('columnheader', { name: '运行次数' })).toBeTruthy()
  })

  it('改名：在「关于」里编辑名称保存，改的就是这个频道', async () => {
    const { emitted } = mountHeader({ can_manage: true })
    await openDetails()
    await fireEvent.click((await screen.findAllByRole('button', { name: '编辑' }))[0])
    const field = document.querySelector('.details__row input') as HTMLInputElement
    await fireEvent.update(field, '登录页改成浅色')
    await fireEvent.click(screen.getByRole('button', { name: '保存' }))
    expect(emitted().rename).toEqual([['登录页改成浅色']])
  })

  it('能归档的人可以在「设置」里归档', async () => {
    mountHeader({ can_manage: true })
    await openDetails('设置')
    await fireEvent.click(await screen.findByRole('button', { name: '归档' }))
    expect(archiveTopic).toHaveBeenCalledWith('topic-1')
  })

  it('频道里还有进行中的任务时不显示归档', async () => {
    mountHeader({ can_manage: true, my_tasks: { shown: [], open: 1 } })
    await openDetails('设置')
    await screen.findByRole('button', { name: '删除频道' })
    expect(screen.queryByRole('button', { name: '归档' })).toBeNull()
  })

  it('已归档的频道在「设置」里取消归档', async () => {
    mountHeader({ status: 'archived', can_manage: true })
    await openDetails('设置')
    await fireEvent.click(await screen.findByRole('button', { name: '取消归档' }))
    expect(unarchiveTopic).toHaveBeenCalledWith('topic-1')
  })

  it('「设置」里改通知，选的是同一份档位', async () => {
    mountHeader({ joined: true })
    await openDetails('设置')
    // 「设置」里环境那一行也有一颗「更改」：这里点的是通知那一行的。
    const level = await screen.findByTestId('details-notify-level')
    const row = level.parentElement?.parentElement as HTMLElement
    await fireEvent.click(within(row).getByRole('button', { name: '更改' }))
    await fireEvent.click(await screen.findByRole('menuitemradio', { name: /所有新消息/ }))
    await waitFor(() => expect(setTopicNotifyLevel).toHaveBeenCalledWith('topic-1', 'all', null))
  })

  it('点那一串头像，打开的是「成员」那一页', async () => {
    mountHeader({ joined: true })
    await fireEvent.click(bar().querySelector('.members-mini') as HTMLElement)
    const tab = await screen.findByRole('tab', { name: '成员' })
    expect(tab.getAttribute('aria-selected')).toBe('true')
  })

  it('别处（侧栏右键、命令面板）要开这个频道的详情，页头接着打开', async () => {
    mountHeader()
    requestChannelDetails('topic-1', 'settings')
    const tab = await screen.findByRole('tab', { name: '设置' })
    expect(tab.getAttribute('aria-selected')).toBe('true')
  })
})

// 删除不可撤销，所以按不动的那一下要有人守着：频道名没打对，删除键就是灰的。
describe('删除频道', () => {
  async function openDelete() {
    await fireEvent.click(screen.getByRole('button', { name: t('work.channelDetails.open', { name: topic.title }) }))
    await fireEvent.click(await screen.findByRole('tab', { name: '设置' }))
    await fireEvent.click(await screen.findByRole('button', { name: '删除频道' }))
    await screen.findByLabelText('频道名称')
  }

  function confirmButton(): HTMLButtonElement {
    const all = screen.getAllByRole('button', { name: '删除频道' }) as HTMLButtonElement[]
    return all[all.length - 1]
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
    await fireEvent.click(screen.getByRole('button', { name: t('work.channelDetails.open', { name: topic.title }) }))
    await fireEvent.click(await screen.findByRole('tab', { name: '设置' }))
    expect(screen.queryByRole('button', { name: '删除频道' })).toBeNull()
  })

  it('「综合」是项目本身，删不得', async () => {
    mountHeader({ kind: 'root', can_manage: true })
    await fireEvent.click(screen.getByRole('button', { name: t('work.channelDetails.open', { name: '综合' }) }))
    await fireEvent.click(await screen.findByRole('tab', { name: '设置' }))
    expect(screen.queryByRole('button', { name: '删除频道' })).toBeNull()
  })
})

// 手机上页头是顶栏，频道那一份操作在顶栏的 ⋯ 里：和侧栏右键、命令面板列的是同一份。
describe('手机上频道顶栏的 ⋯', () => {
  beforeEach(() => {
    window.innerWidth = 390
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

  it('列的是频道那一份：归档、删除不在这里，在详情的「设置」里', async () => {
    mountHeader({ can_manage: true, joined: true })

    await openMore()
    await screen.findByRole('menuitem', { name: '复制链接' })
    const items = screen.getAllByRole('menuitem').map((el) => el.textContent?.trim())
    expect(items).toEqual([
      expect.stringMatching(/^通知/),
      '复制链接',
      '新建任务',
      '全部任务',
      '频道详情',
      '重命名',
      '退出频道',
    ])
  })

  it('「通知」点开换成档位那一层，选静音就静音', async () => {
    mountHeader({ joined: true })

    await openMore()
    await fireEvent.click(await screen.findByRole('menuitem', { name: /通知/ }))
    await fireEvent.click(await screen.findByRole('menuitemradio', { name: /^静音/ }))

    await waitFor(() => expect(setTopicNotifyLevel).toHaveBeenCalledWith('topic-1', 'mute', null))
  })

  it('「频道详情」打开的是详情', async () => {
    mountHeader()

    await openMore()
    await fireEvent.click(await screen.findByRole('menuitem', { name: '频道详情' }))
    expect(await screen.findByRole('tab', { name: '关于' })).toBeTruthy()
  })

  it('已归档的频道不再改名', async () => {
    mountHeader({ status: 'archived', can_manage: true, joined: true })

    await openMore()
    await screen.findByRole('menuitem', { name: '复制链接' })
    expect(screen.queryByRole('menuitem', { name: '重命名' })).toBeNull()
    expect(screen.queryByRole('menuitem', { name: '取消归档' })).toBeNull()
  })
})

describe('频道成员名单里的添加', () => {
  // 每个用例一个项目：项目那一份读进共享的查询缓存里，换个角色得换个项目。
  async function openRosterAs(projectId: string, permissions: string[]) {
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
