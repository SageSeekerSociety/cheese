// 命令面板：⌘K / Ctrl K 叫出来，打几个字（或拼音首字母）找到一个话题、成员、页面，
// 回车就去；# @ > 只看一类；此刻做不了的事不出现；Esc 先清字再关上；拼音还在组字
// 的时候回车不算数；去过的下次打开排在「最近去过」里。在项目里打字还会搜内容（消息、
// 任务……），点开落到它所在的地方；? 只看内容。
import type { ProjectSearchHits } from '@/api'
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
import { t } from '@/i18n'
import { useWorkspaceStore } from '@/stores/workspace'

const searchProject = vi.hoisted(() => vi.fn())
vi.mock('@/api', async (original) => ({ ...(await original<object>()), searchProject }))

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
  created_at: '2026-09-01T00:00:00Z',
  task_id: null,
  snippet: '重试以后队列就不卡了',
}

const Blank = defineComponent({ render: () => h('div') })

function topic(id: string, title: string, extra: Partial<Topic> = {}): Topic {
  return { id, project_id: 'p1', title, kind: 'topic', status: 'active', created_at: '', ...extra } as Topic
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
          { path: 'members/:handle', name: 'member', component: Blank },
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
  const store = useWorkspaceStore()
  store.projectId = 'p1'
  store.projects = [
    { id: 'p1', name: '知是' },
    { id: 'p2', name: '课程助教' },
  ] as never
  store.topics = [
    topic('t1', '登录页改成深色', { awaits_me: true }),
    topic('t2', '搭建第一个原型'),
    topic('t3', '合并队列偶发卡住'),
  ]
  store.members = [{ user_handle: 'alice', name: 'Alice', role: 'member' }] as never

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

  it('打字也搜消息内容，选中一条就进它所在的房间', async () => {
    searchProject.mockImplementation(async (_project: string, q: string) =>
      q === '重试' ? hits({ records: [MESSAGE] }) : NOTHING
    )
    const { router } = await mount()
    await open()
    await type('重试')
    await waitFor(() => expect(options().some((text) => text.includes('重试以后队列就不卡了'))).toBe(true))
    await fireEvent.click(screen.getByText('合并队列偶发卡住', { exact: false, selector: '[role=option] *' }))
    await waitFor(() => expect(router.currentRoute.value.path).toBe('/projects/p1/topics/t3'))
  })

  it('搜到一件任务，选中就进房间并打开那张卡', async () => {
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
    await waitFor(() => expect(router.currentRoute.value.fullPath).toContain('/projects/p1/topics/t2'))
    expect(router.currentRoute.value.query.card).toBe('k9')
  })

  it('说在一件活卡片里的消息，选中就打开那张卡', async () => {
    searchProject.mockResolvedValue(hits({ records: [{ ...MESSAGE, snippet: '卡片里说过的缓存方案', task_id: 'k2' }] }))
    const { router } = await mount()
    await open()
    await type('缓存方案')
    await waitFor(() => expect(options().some((text) => text.includes('卡片里说过的缓存方案'))).toBe(true))
    const row = screen.getAllByRole('option').find((el) => el.textContent?.includes('卡片里说过的缓存方案'))!
    await fireEvent.click(row)
    await waitFor(() => expect(router.currentRoute.value.query.card).toBe('k2'))
    expect(router.currentRoute.value.path).toBe('/projects/p1/topics/t3')
  })

  it('? 只看内容，话题名对上了也不列', async () => {
    searchProject.mockResolvedValue(hits({ records: [{ ...MESSAGE, snippet: '原型的配色再调一下' }] }))
    await mount()
    await open()
    await type('?原型')
    await waitFor(() => expect(options()).toHaveLength(1))
    expect(options()[0]).toContain('原型的配色再调一下')
    expect(searchProject).toHaveBeenLastCalledWith('p1', '原型')
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
