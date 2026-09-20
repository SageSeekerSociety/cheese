/** 进项目的第一屏。
 *
 * `/projects/:id` 自己不显示任何东西，它把人送到一个说得出自己在显示什么的地址。
 * 桌面上那个地址**由这个项目的壳说**（default 壳说：看板）——第一眼该答的是「整个
 * 项目现在什么在跑、什么在等我」，而落进大本营答的是「这一个房间里最近说了什么」。
 *
 * 这一层因此有个新麻烦：以前第一屏是个常量，抬脚就能走；现在它是一份**还没到货的
 * 数据**。所以这份用例真正在盯的是那三种到货方式——浏览器上次见过这个项目（同步）、
 * 没见过（等清单）、等完还是没有（按 default，也就是今天的行为）——以及一条底线：
 * 手机上这一层就是话题列表，一步都不跳；所有已经发出去的 `?topic=` 老链接照旧落在
 * 它们的话题上。
 */
import type { Component } from 'vue'
import type { Project } from '@/cx_types'

import { reactive, ref } from 'vue'
import { render, waitFor } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mdAndUp = ref(true)
vi.mock('vuetify', () => ({ useDisplay: () => ({ mdAndUp }) }))

const replace = vi.fn()
const push = vi.fn()
let query: Record<string, string> = {}
let name = 'workspace-project'
vi.mock('vue-router', () => ({
  useRouter: () => ({ push, replace }),
  useRoute: () => ({
    get query() {
      return query
    },
    get name() {
      return name
    },
  }),
}))

// 手机那一层画的是话题列表本身。这份用例只关心「画了它没有」。
vi.mock('@/views/workspace/ProjectSidebar.vue', () => ({
  default: { name: 'ProjectSidebar', template: '<div data-testid="topic-list" />' },
}))

// 项目的两个来源，各由用例自己摆：**这个浏览器上次见过**的缓存（projectCache，
// App.vue 读的是同一份），和这次会话拉回来的清单（store，由 ProjectShell 触发）。
let cache: Project[] = []
const store = reactive({ projects: [] as Project[], projectsSettled: false })

vi.mock('@/lib/projectCache', () => ({ loadCachedProjects: () => cache }))
vi.mock('@/me', () => ({ myHandle: () => 'lisi' }))
vi.mock('@/stores/workspace', () => ({ useWorkspaceStore: () => store }))

import WorkspaceEntry from './WorkspaceEntry.vue'

const Entry = WorkspaceEntry as unknown as Component

const HOME = 'workspace-running' // default 壳的第一屏
// 一个**编出来**的壳：真壳的名字不该出现在组件或它的用例里。
const OTHER = {
  name: 'my-shell',
  home: 'calendar',
  nav: { rail: [], tabs: [], project: [] },
  hidden: [],
  terms: {},
}

function project(id: string, shell?: unknown): Project {
  return { id, name: id, created_at: '', ...(shell ? { shell } : {}) } as Project
}

beforeEach(() => {
  mdAndUp.value = true
  query = {}
  name = 'workspace-project'
  cache = []
  store.projects = []
  store.projectsSettled = false
  replace.mockReset()
  push.mockReset()
})

function mount() {
  return render(Entry, { props: { projectId: 'p1' } })
}

describe('桌面端: 第一屏听壳的', () => {
  it('没声明壳的项目落在看板上 —— 今天的行为，一个字没变', async () => {
    cache = [project('p1')]
    mount()
    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith({ name: HOME, params: { projectId: 'p1' } })
    )
  })

  it('声明了别的壳就落在别处', async () => {
    cache = [project('p1', OTHER)]
    mount()
    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith({ name: OTHER.home, params: { projectId: 'p1' } })
    )
    expect(replace).not.toHaveBeenCalledWith({ name: HOME, params: { projectId: 'p1' } })
  })

  it('浏览器上次见过这个项目时立刻就走，不等清单', () => {
    // 这是绝大多数进入方式：点一下项目，缓存里就有那一行。等清单只会换来一屏
    // 什么都不画的空转。
    cache = [project('p1', OTHER)]
    mount()
    expect(replace).toHaveBeenCalledTimes(1)
    expect(store.projectsSettled).toBe(false)
  })

  it('没见过就等清单落定，落定后照壳跳', async () => {
    mount()
    expect(replace, '清单还没落定时跳，就是拿一个还不知道的壳去猜第一屏').not.toHaveBeenCalled()
    store.projects = [project('p1', OTHER)]
    store.projectsSettled = true
    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith({ name: OTHER.home, params: { projectId: 'p1' } })
    )
  })

  it('等完还是没有这个项目 —— 按 default 走，谁都不该卡在这一屏', async () => {
    // 清单落定但里面没有 p1：不是我的项目、或者这一趟压根没拉到。这两种都不该
    // 让中转地址变成终点。
    mount()
    store.projectsSettled = true
    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith({ name: HOME, params: { projectId: 'p1' } })
    )
  })

  it('清单还没到货就等着，不拿 default 去猜', async () => {
    // 缓存没有、清单也没到 —— 此刻**没有任何**关于这个壳的信息。猜 default 的
    // 代价是：课程项目的人被送到看板，而第一屏只有一次机会（跳完这一层就卸载了，
    // 没人能再纠一次）。
    mount()
    await new Promise((r) => setTimeout(r, 20))
    expect(replace).not.toHaveBeenCalled()
  })

  it('清单到手但没这一项，就算落定了 —— 不等第二次', async () => {
    // `store.projects` 是整份赋值的，所以「非空」本身就说明那一趟拉完了。
    mount()
    store.projects = [project('other')]
    await waitFor(() => expect(replace).toHaveBeenCalledWith({ name: HOME, params: { projectId: 'p1' } }))
  })

  it('不等话题列表到货 —— 目的地和列表无关，等只会换来一屏转圈', () => {
    cache = [project('p1')]
    const { container } = mount()
    expect(replace).toHaveBeenCalledTimes(1)
    expect(container.querySelector('.v-progress-circular')).toBeNull()
  })

  it('用的是 replace，不是 push —— 后退不该弹回这个中转地址', async () => {
    cache = [project('p1')]
    mount()
    await waitFor(() => expect(replace).toHaveBeenCalled())
    expect(push).not.toHaveBeenCalled()
  })

  it('已经不在 `/projects/:id` 上了就不跳 —— 跳转结束后视图还活着一小会儿', async () => {
    name = 'workspace-running'
    cache = [project('p1')]
    mount()
    await waitFor(() => expect(replace).not.toHaveBeenCalled())
  })
})

describe('手机端一个字都没变', () => {
  it('不跳，这一层画的就是话题列表', async () => {
    mdAndUp.value = false
    cache = [project('p1', OTHER)]
    const { queryByTestId } = mount()
    await waitFor(() => expect(queryByTestId('topic-list')).not.toBeNull())
    expect(replace, '跳走就永远看不到列表，也就没有回上一层可回').not.toHaveBeenCalled()
  })
})

describe('老链接', () => {
  it('?topic= 还是跳那个话题，不是跳第一屏', async () => {
    // 每一条粘贴到聊天里的工作区链接都是这个形状。壳说了什么都不该动它：地址里
    // 已经写明去哪了，没有什么可决定的。
    query = { topic: 't-9' }
    cache = [project('p1', OTHER)]
    mount()
    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith({ name: 'workspace-topic', params: { projectId: 'p1', topicId: 't-9' } })
    )
    expect(replace).toHaveBeenCalledTimes(1)
  })

  it('清单还没到货也照跳话题 —— 老链接不等壳', async () => {
    query = { topic: 't-9' }
    mount()
    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith({ name: 'workspace-topic', params: { projectId: 'p1', topicId: 't-9' } })
    )
  })

  it('手机上的 ?topic= 也照旧', async () => {
    mdAndUp.value = false
    query = { topic: 't-9' }
    mount()
    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith({ name: 'workspace-topic', params: { projectId: 'p1', topicId: 't-9' } })
    )
  })
})
