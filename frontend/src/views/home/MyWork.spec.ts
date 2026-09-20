// 登录后的首页：不进任何项目就能看见我手上的每一个项目，点一张卡直接进去。
//
// 这里测的是**排出来的东西**：卡片上的四样、按壳分的组、按最近活动排的序、手机上
// 只剩一行、以及一个项目都没有的新账号不会撞见一屏空白。
import type { Project, Topic } from '@/cx_types'

import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, waitFor, within } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import MyWork from './MyWork.vue'

import { listAwaitingMe, listProjects, listTopics } from '@/api'
import { setLocale } from '@/i18n'
import { TasksApi } from '@/network/api/tasks'

vi.mock('@/api', async (original) => ({
  ...(await original<typeof import('@/api')>()),
  listProjects: vi.fn(async () => ({ data: [], total: 0 })),
  listTopics: vi.fn(async () => ({ data: [], total: 0 })),
  listAwaitingMe: vi.fn(async () => ({ data: [], total: 0 })),
}))
vi.mock('@/network/api/tasks', () => ({ TasksApi: { detail: vi.fn(async () => ({ data: { task: {} } })) } }))
vi.mock('@/composables/useNewProjectDialog', () => ({
  useNewProjectDialog: () => ({ show: vi.fn() }),
}))

const project = (id: string, name: string, extra: Partial<Project> = {}): Project =>
  ({ id, name, created_at: '2026-09-01T00:00:00Z', ...extra }) as Project

const topic = (id: string, over: Partial<Topic> = {}): Topic =>
  ({
    id,
    project_id: 'p',
    parent_id: null,
    title: '房间',
    kind: 'chat',
    status: 'active',
    created_at: '2026-09-01T00:00:00Z',
    ...over,
  }) as Topic

const shell = (name: string, projectTerm: string) => ({
  name,
  home: 'workspace-running',
  nav: { rail: ['home', 'projects', 'add'], tabs: ['spaces', 'workspace', 'inbox'], project: [] },
  hidden: [],
  terms: { project: projectTerm },
})

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
})

beforeEach(() => {
  setLocale('zh-CN')
  vi.mocked(listProjects).mockResolvedValue({ data: [], total: 0 })
  vi.mocked(listTopics).mockResolvedValue({ data: [], total: 0 })
  vi.mocked(listAwaitingMe).mockResolvedValue({ data: [], total: 0 })
  vi.mocked(TasksApi.detail).mockResolvedValue({ data: { task: {} } } as never)
})

afterEach(cleanup)

const routes = [
  { path: '/work', name: 'HomeWork', component: MyWork },
  { path: '/spaces', name: 'HomeSpaces', component: { template: '<div>空间列表</div>' } },
  { path: '/spaces/:spaceId', name: 'SpaceDetail', component: { template: '<div>空间</div>' } },
  {
    path: '/projects/:projectId',
    name: 'workspace-project',
    component: { template: '<div>工作区</div>' },
    props: true,
  },
]

vi.setConfig({ testTimeout: 20000 })

type View = Awaited<ReturnType<typeof mount>>

/**
 * 在**这一份**渲染里找文字。
 *
 * 不用 `view.findByText`：它问的是 `document.body`，而上一个用例挂的那一份如果
 * 还在（`cleanup` 是 @testing-library/vue 自己在全局 afterEach 里注册的，两个用例
 * 挨得紧时它没那么快），同一个词就命中两份，报「Found multiple elements」——
 * 一条看起来像「页面上多画了一个东西」的假红。用 container 问，问的就只有这一份。
 *
 * 窗口给得比默认的 1s 宽：这一页是「清单先画、动静随后一个个填进去」的，并行跑
 * 用例、机器忙的时候默认窗口会假红。
 */
function findText(view: View, text: string) {
  return within(view.container as HTMLElement).findByText(text, {}, { timeout: 4000 })
}

async function mount(width = 1280) {
  // 上一个用例挂的那一份先真的卸掉：这个仓库里自动 cleanup 不一定及时，
  // 不卸就变成「同一个词命中两份」的假红（见上面 `findText` 那段）。
  cleanup()
  window.innerWidth = width
  window.innerHeight = 866
  const router = createRouter({ history: createMemoryHistory(), routes })
  await router.push('/work')
  await router.isReady()
  const vuetify = createVuetify({ components, directives })
  const view = render(MyWork, { global: { plugins: [vuetify, router, createPinia()] } })
  return { ...view, router }
}

describe('我的工作页', () => {
  it('一个项目都没有时给出下一步，而不是一屏空白', async () => {
    const view = await mount()
    expect(await findText(view, '从这里开始')).toBeTruthy()
    expect(view.getByRole('button', { name: /新建项目/ })).toBeTruthy()
    // 空间那一排也没有内容可画时，整页仍然只有这一块，不是白的。
    expect(view.container.querySelector('.my-work')).toBeTruthy()
  })

  it('四样东西一张卡：项目名、所属空间、最近在发生什么、一页纸总结', async () => {
    vi.mocked(listProjects).mockResolvedValue({
      data: [project('p1', '论文复现', { summary: '一页纸：先把数据管线跑通。', external_task_id: 42 })],
      total: 1,
    })
    vi.mocked(listTopics).mockResolvedValue({
      data: [
        topic('t1', { running: true, last_activity_at: new Date().toISOString() }),
        topic('t2', { running: true }),
      ],
      total: 2,
    })
    vi.mocked(listAwaitingMe).mockResolvedValue({
      data: [
        {
          projectId: 'p1',
          projectName: '论文复现',
          topicId: 't1',
          topicTitle: '房间',
          taskId: null,
          taskTitle: null,
          displayStatus: '待我验收',
          reason: 'reviewer',
          at: '2026-09-01T00:00:00Z',
        },
      ],
      total: 1,
    })
    vi.mocked(TasksApi.detail).mockResolvedValue({
      data: { task: { space: { id: 7, name: '春季课程' } } },
    } as never)

    const view = await mount()
    expect(await findText(view, '论文复现')).toBeTruthy()
    // 所属空间在卡片上（同一页顶部的横排里也有它，所以按卡片里那一行问）。
    await waitFor(() =>
      expect(view.container.querySelector('.my-work__card .my-work__space')?.textContent).toContain('春季课程')
    )
    expect(await findText(view, '2 个在跑')).toBeTruthy()
    expect(await findText(view, '1 件等你')).toBeTruthy()
    expect(await findText(view, '一页纸：先把数据管线跑通。')).toBeTruthy()
  })

  it('按壳分组：组名读这个壳的词表', async () => {
    vi.mocked(listProjects).mockResolvedValue({
      data: [
        project('p1', '办公项目', { shell: shell('workbench', '工作') as never }),
        project('p2', '课程项目', { shell: shell('course-student', '课程') as never }),
        project('p3', '裸项目'),
      ],
      total: 3,
    })
    vi.mocked(listTopics).mockImplementation(async (projectId: string) => ({
      data: [topic(`t-${projectId}`, { last_activity_at: new Date().toISOString() })],
      total: 1,
    }))

    const view = await mount()
    expect(await findText(view, '我的工作')).toBeTruthy()
    expect(await findText(view, '我的课程')).toBeTruthy()
    expect(await findText(view, '我的项目')).toBeTruthy()
  })

  it('最近动过的在那个壳里排前面', async () => {
    vi.mocked(listProjects).mockResolvedValue({
      data: [project('old', '很久没动'), project('new', '刚动过')],
      total: 2,
    })
    vi.mocked(listTopics).mockImplementation(async (projectId: string) => ({
      data: [
        topic(`t-${projectId}`, {
          last_activity_at: projectId === 'new' ? '2026-09-20T00:00:00Z' : '2026-08-01T00:00:00Z',
        }),
      ],
      total: 1,
    }))

    const view = await mount()
    await findText(view, '刚动过')
    const names = Array.from(view.container.querySelectorAll('.my-work__name')).map((el) => el.textContent?.trim())
    expect(names).toEqual(['刚动过', '很久没动'])
  })

  it('卡片点得进去：它是去那个项目工作台的链接', async () => {
    vi.mocked(listProjects).mockResolvedValue({ data: [project('p1', '论文复现')], total: 1 })
    const view = await mount()
    const card = (await findText(view, '论文复现')).closest('a')
    expect(card?.getAttribute('href')).toBe('/projects/p1')
  })

  it('我加入的空间是顶部那一横排，去得成，且「全部空间」还在', async () => {
    vi.mocked(listProjects).mockResolvedValue({
      data: [project('p1', '论文复现', { external_task_id: 42 })],
      total: 1,
    })
    vi.mocked(TasksApi.detail).mockResolvedValue({
      data: { task: { space: { id: 7, name: '春季课程' } } },
    } as never)

    const view = await mount()
    expect(await findText(view, '我加入的空间')).toBeTruthy()
    await waitFor(() =>
      expect(
        Array.from(view.container.querySelectorAll('.my-work__chips a')).map((el) => el.getAttribute('href'))
      ).toContain('/spaces/7')
    )
    // 空间列表降级成这一块之后，`/spaces` 这个地址仍然到得了。
    expect((await findText(view, '全部空间')).closest('a')?.getAttribute('href')).toBe('/spaces')
  })

  // 手机上这一页是**列表**，不是卡片详情：项目名 + 所属空间 + 最近在发生什么一行。
  it('手机上不画一页纸总结，动静仍然是一行', async () => {
    vi.mocked(listProjects).mockResolvedValue({
      data: [project('p1', '论文复现', { summary: '一页纸：先把数据管线跑通。' })],
      total: 1,
    })
    vi.mocked(listTopics).mockResolvedValue({
      data: [topic('t1', { running: true, last_activity_at: new Date().toISOString() })],
      total: 1,
    })

    const view = await mount(390)
    expect(await findText(view, '论文复现')).toBeTruthy()
    await waitFor(() => expect(view.getByText('1 个在跑')).toBeTruthy())
    expect(view.queryByText('一页纸：先把数据管线跑通。')).toBeNull()
    expect(view.container.querySelectorAll('.my-work__summary')).toHaveLength(0)
    expect(view.container.querySelectorAll('.my-work__activity')).toHaveLength(1)
  })

  it('每个项目都在清单里：手机上任何一个项目都点得进去', async () => {
    vi.mocked(listProjects).mockResolvedValue({
      data: [project('p1', '第一个'), project('p2', '第二个'), project('p3', '第三个')],
      total: 3,
    })
    const view = await mount(390)
    await findText(view, '第一个')
    const hrefs = Array.from(view.container.querySelectorAll('.my-work__card')).map((el) => el.getAttribute('href'))
    expect(hrefs).toEqual(['/projects/p1', '/projects/p2', '/projects/p3'])
  })
})
