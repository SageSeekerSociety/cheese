// 命令面板：⌘K / Ctrl K 叫出来，打几个字（或拼音首字母）找到一个话题、成员、页面，
// 回车就去；# @ > 只看一类；此刻做不了的事不出现；Esc 先清字再关上；拼音还在组字
// 的时候回车不算数；去过的下次打开排在「最近去过」里。在项目里打字还会搜内容（消息、
// 任务……），点开落到它所在的地方；? 只看内容。
import type { ProjectSearchHits } from '@/api/projectSearch'
import type { Command } from '@/commands'
import type { Topic } from '@/cx_types'

import { defineComponent, h, ref } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import CommandPalette from './CommandPalette.vue'
import { paletteOpen } from './state'

import { useCommands } from '@/commands'
import { installShortcuts } from '@/commands/shortcuts'
import { setLocale, t } from '@/i18n'
import { useWorkspaceStore } from '@/stores/workspace'
import { seedProject, seedProjects } from '@/test/seedQueries'

const searchProject = vi.hoisted(() => vi.fn())
const archiveTopic = vi.hoisted(() => vi.fn())
const setTopicTitle = vi.hoisted(() => vi.fn())
// 归档之后 store 会重新拉一次话题表。
const listTopics = vi.hoisted(() => vi.fn(async () => ({ data: [], total: 0 })))
// 别的项目里的话题只有名字。
const listTopicNames = vi.hoisted(() =>
  vi.fn(async () => [
    { id: 't1', project_id: 'p1', title: '登录页改成深色', kind: 'topic', status: 'active' },
    { id: 'q1', project_id: 'p2', title: '第三周作业批改', kind: 'topic', status: 'active' },
  ])
)
vi.mock('@/api/projectSearch', async (original) => ({ ...(await original<object>()), searchProject }))
vi.mock('@/api', async (original) => ({
  ...(await original<object>()),
  archiveTopic,
  setTopicTitle,
  listTopics,
  listTopicNames,
}))

const NOTHING: ProjectSearchHits = { records: [], tasks: [], library: [] }
function hits(extra: Partial<ProjectSearchHits>): ProjectSearchHits {
  return { ...NOTHING, ...extra }
}
const MESSAGE = {
  id: 'b1',
  room_id: 't3',
  room_title: '合并队列偶发卡住',
  kind: 'message' as const,
  author: 'alice',
  author_name: 'Alice',
  author_name_source: null,
  created_at: '2026-09-01T00:00:00Z',
  task_id: null,
  snippet: '重试以后队列就不卡了',
}

const Blank = defineComponent({ render: () => h('div') })

function topic(id: string, title: string, extra: Partial<Topic> = {}): Topic {
  return {
    id,
    project_id: 'p1',
    title,
    kind: 'topic',
    status: 'active',
    can_manage: true,
    joined: true,
    created_at: '',
    ...extra,
  } as Topic
}

let undoShortcuts: (() => void) | null = null

async function mount({ withRoomCommand = ref(false) } = {}) {
  window.innerWidth = 1280
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: Blank },
      {
        path: '/projects/:projectId',
        component: Blank,
        children: [
          { path: '', name: 'workspace-project', component: Blank },
          { path: 'topics/:topicId', name: 'workspace-topic', component: Blank },
          { path: 'topics/:topicId/tasks/:taskId', name: 'workspace-task', component: Blank },
          { path: 'members/:handle', name: 'member', component: Blank },
          { path: 'dm/:peer', name: 'workspace-dm', component: Blank },
          { path: 'search', name: 'project-search', component: Blank },
          {
            path: 'library',
            name: 'project-library',
            component: Blank,
            meta: { palette: { label: 'navigation.project.library', icon: 'mdi-folder-outline' } },
          },
        ],
      },
    ],
  })
  await router.push('/projects/p1')
  await router.isReady()

  const pinia = createPinia()
  setActivePinia(pinia)
  seedProjects([
    { id: 'p1', name: '知是' },
    { id: 'p2', name: '课程助教' },
  ] as never)
  seedProject('p1', {
    topics: [
      topic('t1', '登录页改成深色', { awaits_me: true }),
      topic('t2', '搭建第一个原型'),
      topic('t3', '合并队列偶发卡住'),
    ],
    members: [{ user_handle: 'alice', name: 'Alice', role: 'member' }] as never,
    unread: {},
    privateUnread: {},
    notifyLevels: {},
  })
  useWorkspaceStore().openProject('p1')

  const run = vi.fn()
  const hidden = vi.fn()
  const Room = defineComponent({
    setup() {
      useCommands((): Command[] => [
        { id: 'room.rename', title: '重命名', icon: 'mdi-pencil-outline', run },
        { id: 'room.refresh', title: '刷新房间', icon: 'mdi-refresh', palette: false, run: hidden },
      ])
      return () => h('div')
    },
  })
  const Host = defineComponent({
    setup: () => () => h(components.VApp, null, () => [h(CommandPalette), withRoomCommand.value ? h(Room) : null]),
  })
  const view = render(Host, { global: { plugins: [createVuetify({ components, directives }), router, pinia] } })
  undoShortcuts = installShortcuts(router)
  return { ...view, router, run, hidden, withRoomCommand }
}

const field = () => screen.queryByRole('combobox') as HTMLInputElement | null
const options = () => screen.queryAllByRole('option').map((el) => el.textContent?.trim() ?? '')

async function open() {
  await fireEvent.keyDown(window, { key: 'k', code: 'KeyK', ctrlKey: true })
  await waitFor(() => expect(field()).not.toBeNull())
}

async function type(text: string) {
  await fireEvent.update(field()!, text)
}

async function press(key: string, init: KeyboardEventInit = {}) {
  await fireEvent.keyDown(field()!, { key, ...init })
}

beforeEach(() => {
  archiveTopic.mockReset()
  archiveTopic.mockImplementation(async (id: string) => ({ id, status: 'archived' }))
  setTopicTitle.mockReset()
  setTopicTitle.mockImplementation(async (id: string, title: string) => ({ id, title }))
  searchProject.mockReset()
  searchProject.mockResolvedValue(NOTHING)
  localStorage.clear()
  // 面板开没开着是整个应用共用的一格；上一条测试结束时开着的，别带进下一条。
  paletteOpen.value = false
})

afterEach(() => {
  undoShortcuts?.()
  cleanup()
  document.body.innerHTML = ''
})

describe('命令面板', () => {
  it('Ctrl K 叫出来，光标就在输入框里；再按一次收起来', async () => {
    await mount()
    await open()
    await waitFor(() => expect(document.activeElement).toBe(field()))
    await fireEvent.keyDown(window, { key: 'k', code: 'KeyK', ctrlKey: true })
    await waitFor(() => expect(field()).toBeNull())
  })

  it('打话题名里的几个字，回车就进那个话题', async () => {
    const { router } = await mount()
    await open()
    await type('原型')
    await press('Enter')
    await waitFor(() => expect(router.currentRoute.value.path).toBe('/projects/p1/topics/t2'))
    expect(field()).toBeNull()
  })

  it('拼音首字母也找得到', async () => {
    const { router } = await mount()
    await open()
    await type('hbdl')
    await press('Enter')
    await waitFor(() => expect(router.currentRoute.value.path).toBe('/projects/p1/topics/t3'))
  })

  it('# 只看话题，@ 只看成员，> 只看操作', async () => {
    await mount({ withRoomCommand: ref(true) })
    await open()
    await type('#')
    await waitFor(() => expect(options()).toHaveLength(3))
    expect(options().some((text) => text.includes('Alice'))).toBe(false)

    await type('@')
    await waitFor(() => expect(options()).toHaveLength(1))
    expect(options()[0]).toContain('Alice')

    await type('>')
    await waitFor(() => expect(options().some((text) => text.includes('重命名'))).toBe(true))
    expect(options().some((text) => text.includes('登录页'))).toBe(false)
  })

  it('页面、项目也在里面，选了就去', async () => {
    const { router } = await mount()
    await open()
    await type(t('navigation.project.library'))
    await press('Enter')
    await waitFor(() => expect(router.currentRoute.value.path).toBe('/projects/p1/library'))

    await open()
    await type('课程')
    await press('Enter')
    await waitFor(() => expect(router.currentRoute.value.path).toBe('/projects/p2'))
  })

  it('房间在的时候它的操作在面板里，选了就做；离开房间就没了', async () => {
    const withRoomCommand = ref(true)
    const { run } = await mount({ withRoomCommand })
    await open()
    await type('重命名')
    await press('Enter')
    expect(run).toHaveBeenCalledTimes(1)

    withRoomCommand.value = false
    await open()
    await type('重命名')
    await waitFor(() => expect(options()).toHaveLength(0))
  })

  it('标了不进面板的操作找不到', async () => {
    const { hidden } = await mount({ withRoomCommand: ref(true) })
    await open()
    await type('刷新')
    await waitFor(() => expect(options()).toHaveLength(0))
    await press('Enter')
    expect(hidden).not.toHaveBeenCalled()
  })

  it('Esc 先清掉输入，空着再按才关上', async () => {
    await mount()
    await open()
    await type('原型')
    await press('Escape')
    expect(field()?.value).toBe('')
    expect(field()).not.toBeNull()
    await press('Escape')
    await waitFor(() => expect(field()).toBeNull())
  })

  it('拼音还在组字时按回车不算选中', async () => {
    const { router } = await mount()
    await open()
    await type('原型')
    await press('Enter', { isComposing: true } as KeyboardEventInit)
    expect(router.currentRoute.value.path).toBe('/projects/p1')
    expect(field()).not.toBeNull()
  })

  it('不打字时，等你处理的在最上面；去过的下次打开排在「最近去过」里', async () => {
    await mount()
    await open()
    const listbox = screen.getByRole('listbox')
    expect(
      within(listbox).getByText(t('navigation.palette.awaiting'), { selector: '[role=presentation]' })
    ).toBeTruthy()
    expect(options()[0]).toContain('登录页改成深色')

    await type('原型')
    await press('Enter')
    await waitFor(() => expect(field()).toBeNull())

    await open()
    const recent = within(screen.getByRole('listbox')).getByText(t('navigation.palette.recent'))
    const afterRecent = recent.nextElementSibling
    expect(afterRecent?.textContent).toContain('搭建第一个原型')
  })

  it('打字也搜消息内容，选中一条就进它所在的房间，停在那一条上', async () => {
    searchProject.mockImplementation(async (_project: string, q: string) =>
      q === '重试' ? hits({ records: [MESSAGE] }) : NOTHING
    )
    const { router } = await mount()
    await open()
    await type('重试')
    await waitFor(() => expect(options().some((text) => text.includes('重试以后队列就不卡了'))).toBe(true))
    await fireEvent.click(screen.getByText('合并队列偶发卡住', { exact: false, selector: '[role=option] *' }))
    await waitFor(() => expect(router.currentRoute.value.path).toBe('/projects/p1/topics/t3'))
    expect(router.currentRoute.value.query.block).toBe('b1')
  })

  it('搜到一件任务，选中就打开那个任务', async () => {
    searchProject.mockResolvedValue(
      hits({
        tasks: [
          { id: 'k9', room_id: 't2', room_title: '搭建第一个原型', title: '写登录接口', status: 'open', snippet: '' },
        ],
      })
    )
    const { router } = await mount()
    await open()
    await type('登录接口')
    await waitFor(() => expect(options().some((text) => text.includes('写登录接口'))).toBe(true))
    const row = screen.getAllByRole('option').find((el) => el.textContent?.includes('写登录接口'))!
    await fireEvent.click(row)
    await waitFor(() => expect(router.currentRoute.value.path).toBe('/projects/p1/topics/t2/tasks/k9'))
  })

  it('说在任务里的消息，选中就打开那个任务，停在那一条上', async () => {
    searchProject.mockResolvedValue(hits({ records: [{ ...MESSAGE, snippet: '卡片里说过的缓存方案', task_id: 'k2' }] }))
    const { router } = await mount()
    await open()
    await type('缓存方案')
    await waitFor(() => expect(options().some((text) => text.includes('卡片里说过的缓存方案'))).toBe(true))
    const row = screen.getAllByRole('option').find((el) => el.textContent?.includes('卡片里说过的缓存方案'))!
    await fireEvent.click(row)
    await waitFor(() => expect(router.currentRoute.value.path).toBe('/projects/p1/topics/t3/tasks/k2'))
    expect(router.currentRoute.value.query.block).toBe('b1')
  })

  it('? 只看内容，话题名对上了也不列', async () => {
    searchProject.mockResolvedValue(hits({ records: [{ ...MESSAGE, snippet: '原型的配色再调一下' }] }))
    await mount()
    await open()
    await type('?原型')
    await waitFor(() => expect(options().some((text) => text.includes('原型的配色再调一下'))).toBe(true))
    expect(options().some((text) => text.includes('搭建第一个原型'))).toBe(false)
    expect(searchProject).toHaveBeenLastCalledWith('p1', '原型')
  })

  it('内容结果下面写作者现在的名字，前面带 @；没有名字就写 handle，不带 @', async () => {
    searchProject.mockResolvedValue(
      hits({
        records: [
          { ...MESSAGE, id: 'b1', snippet: '署名一', author: 'alice', author_name: 'Alice Chen' },
          { ...MESSAGE, id: 'b2', snippet: '署名二', author: 'bob-7', author_name: null },
          {
            ...MESSAGE,
            id: 'c3',
            kind: 'comment',
            snippet: '署名三',
            author: 'cheese-kimi',
            author_name: 'Kimi',
            author_name_source: 'human',
          },
        ],
      })
    )
    await mount()
    await open()
    await type('署名')
    const row = (snippet: string) => options().find((text) => text.includes(snippet)) ?? ''
    await waitFor(() => expect(row('署名三')).not.toBe(''))
    expect(row('署名一')).toContain('@Alice Chen')
    expect(row('署名一')).not.toContain('alice')
    expect(row('署名二')).toContain('bob-7')
    expect(row('署名二')).not.toContain('@bob-7')
    expect(row('署名三')).toContain('@Kimi')
    expect(row('署名三')).not.toContain('cheese-kimi')
  })

  it('没改过名的队友按读者的语言称呼', async () => {
    setLocale('en')
    try {
      searchProject.mockResolvedValue(
        hits({
          records: [
            {
              ...MESSAGE,
              snippet: '默认署名',
              author: 'cheese-0a1b',
              author_name: '芝士',
              author_name_source: 'default',
            },
          ],
        })
      )
      await mount()
      await open()
      await type('默认署名')
      await waitFor(() => expect(options().some((text) => text.includes('默认署名'))).toBe(true))
      const row = options().find((text) => text.includes('默认署名'))!
      expect(row).toContain('@Cheese')
      expect(row).not.toContain('芝士')
    } finally {
      setLocale('zh-CN')
    }
  })

  it('内容结果的最后一行进搜索结果页，带着这个词', async () => {
    searchProject.mockImplementation(async (_project: string, q: string) =>
      q === '配色' ? hits({ records: [{ ...MESSAGE, snippet: '配色再调一下' }] }) : NOTHING
    )
    const { router } = await mount()
    await open()
    await type('配色')
    const last = t('navigation.palette.searchAll')
    await waitFor(() => expect(options().at(-1)).toContain(last))
    for (let i = 1; i < options().length; i++) await press('ArrowDown')
    await press('Enter')
    await waitFor(() => expect(router.currentRoute.value.path).toBe('/projects/p1/search'))
    expect(router.currentRoute.value.query.q).toBe('配色')
  })

  it('什么内容都没搜到时，没有「查看全部结果」', async () => {
    await mount()
    await open()
    await type('根本没有')
    await new Promise((resolve) => setTimeout(resolve, 300))
    expect(options().some((text) => text.includes(t('navigation.palette.searchAll')))).toBe(false)
  })

  // 同一个词短时间内只问一次后端（五个数据源合用），所以各条用例用的词互不相同。
  it('字已经改了，上一次输入搜回来的内容不出现', async () => {
    let answerOld: (value: ProjectSearchHits) => void = () => {}
    searchProject.mockImplementation((_project: string, q: string) =>
      q === '队列' ? new Promise<ProjectSearchHits>((resolve) => (answerOld = resolve)) : Promise.resolve(NOTHING)
    )
    await mount()
    await open()
    await type('队列')
    await waitFor(() => expect(searchProject).toHaveBeenCalledWith('p1', '队列'))
    await type('深色')
    await waitFor(() => expect(searchProject).toHaveBeenCalledWith('p1', '深色'))
    answerOld(hits({ records: [MESSAGE] }))
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(options().some((text) => text.includes('重试以后队列就不卡了'))).toBe(false)
  })
})

// Tab：选中一条再按 Tab，列出对它还能做的事。清单里的事做的是选中的那一条；Esc 只收起
// 清单，面板还在；在面板里重命名不离开当前页，回车改名，Esc 不改。
describe('命令面板：更多操作', () => {
  const actions = () => screen.queryAllByRole('menuitem').map((el) => el.textContent?.trim() ?? '')
  async function choose(name: string) {
    await waitFor(() => expect(actions()).toContain(name))
    const index = actions().indexOf(name)
    for (let i = 0; i < index; i++) await press('ArrowDown')
    await press('Enter')
  }

  it('在一个话题上按 Tab，归档的是这个话题，页面不动', async () => {
    const { router } = await mount()
    await open()
    await type('原型')
    await press('Tab')
    await choose(t('work.room.menu.archive'))
    await waitFor(() => expect(archiveTopic).toHaveBeenCalledWith('t2'))
    expect(router.currentRoute.value.path).toBe('/projects/p1')
    await waitFor(() => expect(field()).toBeNull())
  })

  it('Esc 只收起清单，面板和输入都还在', async () => {
    await mount()
    await open()
    await type('原型')
    await press('Tab')
    await waitFor(() => expect(actions().length).toBeGreaterThan(0))
    await press('Escape')
    await waitFor(() => expect(actions()).toHaveLength(0))
    expect(field()?.value).toBe('原型')
  })

  it('在面板里重命名：改了名字回车就改，留在原来的页面', async () => {
    const { router } = await mount()
    await open()
    await type('原型')
    await press('Tab')
    await choose(t('work.room.menu.rename'))
    const box = await screen.findByLabelText(t('work.room.menu.renameTitle'))
    await fireEvent.update(box, '搭建第二个原型')
    await fireEvent.keyDown(box, { key: 'Enter' })
    await waitFor(() => expect(setTopicTitle).toHaveBeenCalledWith('t2', '搭建第二个原型'))
    expect(router.currentRoute.value.path).toBe('/projects/p1')
  })

  it('重命名时按 Esc 不改名，回到结果列表', async () => {
    await mount()
    await open()
    await type('原型')
    await press('Tab')
    await choose(t('work.room.menu.rename'))
    const box = await screen.findByLabelText(t('work.room.menu.renameTitle'))
    await fireEvent.update(box, '不要的名字')
    await fireEvent.keyDown(box, { key: 'Escape' })
    await waitFor(() => expect(field()?.value).toBe('原型'))
    expect(setTopicTitle).not.toHaveBeenCalled()
  })

  it('给成员发私信', async () => {
    const { router } = await mount()
    await open()
    await type('@alice')
    await press('Tab')
    await choose(t('navigation.palette.dm'))
    await waitFor(() => expect(router.currentRoute.value.path).toBe('/projects/p1/dm/alice'))
  })

  it('一条消息的「打开所在话题」进房间，不停在那一条上', async () => {
    searchProject.mockImplementation(async (_project: string, q: string) =>
      q === '不卡了' ? hits({ records: [MESSAGE] }) : NOTHING
    )
    const { router } = await mount()
    await open()
    await type('不卡了')
    await waitFor(() => expect(options().some((text) => text.includes('重试以后队列就不卡了'))).toBe(true))
    const index = options().findIndex((text) => text.includes('重试以后队列就不卡了'))
    for (let i = 0; i < index; i++) await press('ArrowDown')
    await press('Tab')
    await choose(t('navigation.palette.openRoom'))
    await waitFor(() => expect(router.currentRoute.value.path).toBe('/projects/p1/topics/t3'))
    expect(router.currentRoute.value.query.block).toBeUndefined()
  })
})

// 范围：默认在当前项目里。输入框空着按退格去掉范围，跨项目只找名字（话题、项目），
// 不搜内容；在一个项目上按 Tab，就进到那个项目里搜。重新打开回到当前项目。
describe('命令面板：范围', () => {
  it('空着按退格，别的项目里的话题也找得到，内容不搜', async () => {
    const { router } = await mount()
    await open()
    await type('作业')
    await waitFor(() => expect(options()).toHaveLength(0))
    await type('')
    await press('Backspace')
    await type('作业')
    await waitFor(() => expect(options().some((text) => text.includes('第三周作业批改'))).toBe(true))
    await new Promise((resolve) => setTimeout(resolve, 300))
    expect(searchProject).not.toHaveBeenCalledWith(expect.anything(), '作业')
    await press('Enter')
    await waitFor(() => expect(router.currentRoute.value.path).toBe('/projects/p2/topics/q1'))
  })

  it('在一个项目上按 Tab，接着打的字在那个项目里搜', async () => {
    await mount()
    await open()
    await type('课程')
    await waitFor(() => expect(options().some((text) => text.includes('课程助教'))).toBe(true))
    await press('Tab')
    await waitFor(() => expect(field()?.value).toBe(''))
    await type('批改')
    await waitFor(() => expect(options().some((text) => text.includes('第三周作业批改'))).toBe(true))
    // 当前项目的话题不再列出来。
    await type('原型')
    await waitFor(() => expect(searchProject).toHaveBeenCalledWith('p2', '原型'))
    expect(options().some((text) => text.includes('搭建第一个原型'))).toBe(false)
  })

  it('重新打开回到当前项目', async () => {
    await mount()
    await open()
    await press('Backspace')
    await fireEvent.keyDown(window, { key: 'k', code: 'KeyK', ctrlKey: true })
    await waitFor(() => expect(field()).toBeNull())
    await open()
    await type('作业')
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(options().some((text) => text.includes('第三周作业批改'))).toBe(false)
  })
})
