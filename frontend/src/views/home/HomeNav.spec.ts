// 首页那一格的目录，只看画面这一半：名单从 props 来，点下去发出事件；正在看的那个团队
// 一定是展开的，团队行的 ⋯ 和右键按角色长。
//
// 名单是怎么读来的、点下去之后真发生了什么（退出、解散、转让、加入）在
// `composables/useTeamNav.spec.ts` 里，那里挂的是真接线。
import type { Component } from 'vue'
import type { SpaceLink, Team, TeamMember } from '@/types'

import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import HomeNav from './HomeNav.vue'

import { setLocale } from '@/i18n'

const blank = { template: '<div />' }

function team(handle: string, name: string, role: Team['role'] = 'MEMBER', extra: Partial<Team> = {}): Team {
  return {
    id: [...handle].reduce((n, c) => n + c.charCodeAt(0), 0),
    handle,
    name,
    intro: '',
    avatarId: 1,
    role,
    owner: { id: 1 } as Team['owner'],
    admins: { total: 0, examples: [] },
    members: { total: 1, examples: [] },
    ...extra,
  }
}

function member(id: number, nickname: string): TeamMember {
  return { role: 'MEMBER', user: { id, username: `${nickname}-handle`, nickname, avatarId: 0 } } as TeamMember
}

/** 一份目录要的那些值，缺的都当「没有」——每一件只写它在乎的那几样。 */
function props(overrides: Record<string, unknown> = {}) {
  return {
    inbox: true,
    teams: [] as Team[],
    spaces: [] as SpaceLink[],
    disbanding: null,
    transferring: null,
    transferCandidates: [] as TeamMember[],
    joinOpen: false,
    ...overrides,
  }
}

async function mount(overrides: Record<string, unknown> = {}) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/inbox', name: 'inbox', component: blank },
      // 侧栏里那份目录（`HomeNav.vue`）多了一行通往「市场」：名字不在表里，带 `to`
      // 的行会当场抛，整个侧栏就渲染不出来。
      { path: '/market', name: 'market', component: blank },
      { path: '/space-applications/:spaceId', name: 'HomeSpaceApplication', component: blank },
      { path: '/spaces/:spaceId/tasks', name: 'SpacesDetailTasksList', component: blank },
      { path: '/teams/explore', name: 'HomeTeamsExplore', component: blank },
      { path: '/users/settings/compute', name: 'UserSettingsCompute', component: blank },
      {
        path: '/teams/:handle',
        component: { template: '<router-view />' },
        children: [
          { path: '', name: 'TeamsDetailDefault', component: blank },
          { path: 'members', name: 'TeamsDetailMembers', component: blank },
          { path: 'settings', name: 'TeamsDetailSettings', component: blank },
          { path: 'profile', name: 'TeamsDetailProfile', component: blank },
          { path: 'library', name: 'TeamsDetailLibrary', component: blank },
          { path: 'compute', name: 'TeamsDetailCompute', component: blank },
          { path: 'models', name: 'TeamsDetailModels', component: blank },
          { path: 'credits', name: 'TeamsDetailCredits', component: blank },
        ],
      },
    ],
  })
  await router.push('/inbox')
  await router.isReady()
  const view = render(HomeNav as unknown as Component, {
    props: props(overrides),
    global: { plugins: [createVuetify({ components, directives }), router] },
  })
  return { view, router }
}

beforeAll(() => {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
  // 团队操作是个 v-menu，打开时要量视口；jsdom 两样都没有。
  vi.stubGlobal('devicePixelRatio', 1)
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    scale: 1,
    addEventListener() {},
    removeEventListener() {},
  })
})
beforeEach(() => {
  setLocale('zh-CN')
  localStorage.clear()
})
afterEach(cleanup)

const hrefs = () => Array.from(document.querySelectorAll('a')).map((a) => a.getAttribute('href'))

/** 点开一行 ⋯ 之后，弹出来的那份操作清单。 */
function menuLabels() {
  return Array.from(document.querySelectorAll('.v-overlay .v-list-item-title')).map((el) => el.textContent?.trim())
}

describe('首页目录的画面', () => {
  it('正在看的那个团队展开着，项目和资料库都点得到；别的团队收着', async () => {
    await mount({
      teams: [team('crew', '知是开发组'), team('lab', '数据课第三组')],
      currentHandle: 'crew',
    })
    await waitFor(() => expect(hrefs()).toEqual(expect.arrayContaining(['/teams/crew', '/teams/crew/library'])))
    expect(hrefs()).not.toContain('/teams/crew/docs')
    expect(hrefs().some((href) => href?.startsWith('/teams/lab'))).toBe(false)
  })

  // 市场（技能、插件、MCP、Agent 模板）全站一份，入口只在首页这份目录里，挨着空间：
  // 桌面侧栏和手机「首页」整页都画这一份，两端从同一行进。
  it('「市场」那一行挨着空间，排在空间那一段之后、团队之外', async () => {
    const space = { id: 7, name: '数据课', avatarId: null } as unknown as SpaceLink
    await mount({ teams: [team('crew', '知是开发组')], spaces: [space] })
    await screen.findByText('知是开发组')
    expect(screen.getByText('市场')).toBeTruthy()
    const order = hrefs()
    const market = order.indexOf('/market')
    expect(market).toBeGreaterThan(-1)
    // 空间那一段（标题、空间、加入 / 创建）之后才是市场；团队那一段在它们前面。
    expect(market).toBeGreaterThan(order.indexOf('/spaces/7/tasks'))
    expect(market).toBeGreaterThan(order.indexOf('/teams/explore'))
    const labels = Array.from(document.querySelectorAll('.v-list-subheader, .v-list-item-title')).map((el) =>
      el.textContent?.trim()
    )
    expect(labels.indexOf('市场')).toBeGreaterThan(labels.indexOf('新建空间'))
  })

  it('点一个团队在原地展开，再点收起', async () => {
    await mount({ teams: [team('lab', '数据课第三组')] })
    await fireEvent.click(await screen.findByLabelText('展开 数据课第三组'))
    await waitFor(() => expect(hrefs()).toContain('/teams/lab/library'))
    await fireEvent.click(screen.getByLabelText('收起 数据课第三组'))
    await waitFor(() => expect(hrefs()).not.toContain('/teams/lab/library'))
  })

  it.each([
    ['ADMIN', ['设置', '邀请成员', '编辑团队资料', '退出团队']],
    ['MEMBER', ['设置', '退出团队']],
    ['OWNER', ['设置', '邀请成员', '编辑团队资料', '转让团队', '解散团队']],
  ] as const)('%s 在团队那一行的 ⋯ 里看到的操作', async (role, labels) => {
    await mount({ teams: [team('crew', '知是开发组', role)] })
    await fireEvent.click(await screen.findByLabelText('团队操作'))
    await screen.findByText(labels[0])
    expect(menuLabels()).toEqual(labels)
  })

  it.each([
    ['OWNER', '解散团队', 'openDisband'],
    ['OWNER', '转让团队', 'openTransfer'],
    ['MEMBER', '退出团队', 'leave'],
  ] as const)('从 ⋯ 里选「%s」只是把这件事报上去', async (role, label, event) => {
    const crew = team('crew', '知是开发组', role)
    const { view } = await mount({ teams: [crew] })
    await fireEvent.click(await screen.findByLabelText('团队操作'))
    await fireEvent.click(await screen.findByText(label))
    await waitFor(() => expect(view.emitted(event)).toEqual([[crew]]))
  })

  it('右键一个团队弹出同一份操作', async () => {
    await mount({ teams: [team('lab', '数据课第三组')] })
    await fireEvent.contextMenu(await screen.findByLabelText('展开 数据课第三组'), { clientX: 40, clientY: 80 })
    expect(await screen.findByText('退出团队')).toBeTruthy()
  })

  // 自己名下的项目不是一个团队：以自己的昵称单独一行，在「团队」小标题之上；
  // 它的 ⋯ 里只有「设置」，打开的是个人设置：自己名下的设备、模型、额度在那里。
  it('自己名下在「团队」之上单独一行，⋯ 里只有「设置」', async () => {
    const own = team('andy', '林夏', 'OWNER', { personal: true })
    await mount({ teams: [own], currentHandle: 'andy' })
    await waitFor(() => expect(hrefs()).toEqual(expect.arrayContaining(['/teams/andy', '/teams/andy/library'])))
    const text = document.body.textContent ?? ''
    expect(text.indexOf('林夏')).toBeLessThan(text.indexOf('团队'))

    await fireEvent.click(await screen.findByLabelText('团队操作'))
    await screen.findByText('设置')
    expect(menuLabels()).toEqual(['设置'])
    expect(screen.getByText('设置').closest('a')?.getAttribute('href')).toBe('/users/settings/compute')
  })

  it('成员、设备、模型、额度不在侧栏上，从 ⋯ 的「设置」进去', async () => {
    await mount({ teams: [team('crew', '知是开发组')], currentHandle: 'crew' })
    await waitFor(() => expect(hrefs()).toContain('/teams/crew/library'))
    for (const page of ['members', 'compute', 'models', 'credits']) expect(hrefs()).not.toContain(`/teams/crew/${page}`)
    await fireEvent.click(await screen.findByLabelText('团队操作'))
    await screen.findByText('设置')
    expect(screen.getByText('设置').closest('a')?.getAttribute('href')).toBe('/teams/crew/settings')
  })

  it('「编辑团队资料」打开团队设置的「基本信息」', async () => {
    await mount({ teams: [team('crew', '知是开发组', 'OWNER')] })
    await fireEvent.click(await screen.findByLabelText('团队操作'))
    expect((await screen.findByText('编辑团队资料')).closest('a')?.getAttribute('href')).toBe('/teams/crew/profile')
  })

  it('空间点了就进那个空间', async () => {
    await mount({ spaces: [{ id: 3, name: '数据分析课' }] })
    await screen.findByText('数据分析课')
    expect(hrefs()).toContain('/spaces/3/tasks')
  })

  it('空间挑过头像就画那一张，没挑过画彩色首字母', async () => {
    await mount({
      spaces: [
        { id: 3, name: '数据分析课', avatarId: 7 },
        { id: 4, name: '城市数据实验室', avatarId: null },
      ],
    })
    const picked = (await screen.findByText('数据分析课')).closest('.v-list-item')
    expect(picked?.querySelector('.home-nav__mark img')?.getAttribute('src')).toContain('/avatars/7')
    const plain = screen.getByText('城市数据实验室').closest('.v-list-item')
    expect(plain?.querySelector('.home-nav__mark img')).toBeNull()
  })

  it('待我处理的件数画在「待办」那一行上，一百以上收成 99+', async () => {
    await mount({ awaiting: 3 })
    expect(await screen.findByText('3')).toBeTruthy()
    cleanup()
    await mount({ awaiting: 128 })
    expect(await screen.findByText('99+')).toBeTruthy()
  })

  it('点「用邀请码加入」那一行：这件事由外面开', async () => {
    const { view } = await mount()
    await fireEvent.click(await screen.findByText('用邀请码加入'))
    await waitFor(() => expect(view.emitted('update:joinOpen')).toEqual([[true]]))
  })
})

describe('目录带的四个弹窗', () => {
  let view: ReturnType<typeof render>

  it('解散：要把团队名打一遍才报得上去', async () => {
    const crew = team('crew', '知是开发组', 'OWNER')
    view = (await mount({ teams: [crew], disbanding: crew })).view
    const confirmButton = await screen.findByRole('button', { name: '解散团队' })
    const input = screen.getByLabelText('输入团队名「知是开发组」确认')

    await fireEvent.update(input, '知是')
    expect(confirmButton.hasAttribute('disabled')).toBe(true)
    await fireEvent.click(confirmButton)
    expect(view.emitted('confirmDisband')).toBeUndefined()

    await fireEvent.update(input, '知是开发组')
    await waitFor(() => expect(confirmButton.hasAttribute('disabled')).toBe(false))
    await fireEvent.click(confirmButton)
    await waitFor(() => expect(view.emitted('confirmDisband')).toEqual([[]]))
  })

  it('解散被拒的理由：由外面给回来，画在弹窗里', async () => {
    const crew = team('crew', '知是开发组', 'OWNER')
    await mount({
      teams: [crew],
      disbanding: crew,
      disbandError: '团队里还有没归档的项目，把它们都归档后才能解散',
    })
    expect(await screen.findByText('团队里还有没归档的项目，把它们都归档后才能解散')).toBeTruthy()
  })

  it('转让：名单由外面给，挑中谁就把谁的 id 报上去', async () => {
    const crew = team('crew', '知是开发组', 'OWNER')
    view = (await mount({ teams: [crew], transferring: crew, transferCandidates: [member(7, '小王')] })).view
    await fireEvent.click(await screen.findByText('小王'))
    await fireEvent.click(screen.getByRole('button', { name: '转让团队' }))
    await waitFor(() => expect(view.emitted('confirmTransfer')).toEqual([[7]]))
  })

  it('进来接手的名单还没到，别说成「团队里没有别人」', async () => {
    const crew = team('crew', '知是开发组', 'OWNER')
    await mount({ teams: [crew], transferring: crew, transferCandidates: [], transferLoading: true })
    expect(screen.queryByText('团队里还没有别的成员可以接手。')).toBeNull()
  })

  it('加入空间：弹窗开着时填好邀请码，按「加入」把码报上去', async () => {
    view = (await mount({ joinOpen: true })).view
    await fireEvent.update(await screen.findByLabelText('邀请码'), ' abc123 ')
    await fireEvent.click(screen.getByRole('button', { name: '加入' }))
    await waitFor(() => expect(view.emitted('join')).toEqual([['abc123']]))
  })
})
