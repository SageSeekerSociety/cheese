// 空间首页接真数据这条路上最容易错的三件事，各钉一条：
//
// 1. **列表只放已上板的题**。`GET /tasks` 那条列表里待审的题也在（对管理员）；
//    首页要是把它们和上板的混在一起，等于把「审核」这一步在界面上抹掉了。
// 2. **待审那块只给管理员看**，而且它走的是另一次请求（列表接口对非管理员根本
//    不含待审题）。这两点分开钉：普通成员看不到那一块，管理员看得到。
// 3. 角色是**空间装完之后**才算出来的 —— 所以「管理员的首页」这条用例里，
//    `loadBoard` 必须先跑完；顺序错了那块提示就不出现。
import type { Component } from 'vue'

import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const spaceDetail = vi.fn()
const listTasks = vi.fn()

vi.mock('@/network/api/spaces', () => ({
  SpacesApi: {
    detail: (...a: unknown[]) => spaceDetail(...a),
    listInviteCodes: vi.fn(async () => ({ data: { inviteCodes: [] } })),
  },
}))

vi.mock('@/network/api/tasks', () => ({
  TasksApi: {
    list: (...a: unknown[]) => listTasks(...a),
  },
}))

vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

import { loadBoard } from '../store'

import BoardHome from './BoardHome.vue'

const SPACE_ID = 11

const SPACE = {
  id: SPACE_ID,
  name: '数据结构空间',
  intro: '',
  avatarId: null,
  admins: [{ user: { id: 4, username: 'caisongyang', nickname: '蔡松洋' }, role: 'OWNER' }],
  announcements: '[]',
  taskTemplates: '[]',
  classificationTopics: [],
  visibleTaskLimit: null,
}

/** 真 `Task` 的字段子集 —— 首页只读这些。 */
function task(over: Record<string, unknown>) {
  return {
    id: 1,
    name: '一道题',
    intro: '简介',
    approved: 'APPROVED',
    participantLimit: 0,
    minTeamSize: 1,
    maxTeamSize: 1,
    deadline: null,
    createdAt: Date.now(),
    participants: { total: 3, examples: [] },
    creator: { id: 4, username: 'caisongyang', nickname: '蔡松洋' },
    ...over,
  }
}

const PUBLISHED = task({ id: 1, name: '已上板的题' })
const PENDING = task({ id: 2, name: '还在等审的题', approved: 'NONE' })

/** 点名前先落一个登录态 —— 角色是拿它跟 `space.admins` 对出来的。 */
function signIn(handle: string | null) {
  if (handle === null) localStorage.removeItem('user')
  else localStorage.setItem('user', JSON.stringify({ id: 4, username: handle, nickname: '蔡松洋' }))
}

const Page = defineComponent({ render: () => h(RouterView) })

async function mount() {
  const stub = { render: () => h('div') }
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/spaces/:spaceId/board', name: 'SpaceBoardHome', component: BoardHome as Component },
      { path: '/spaces/:spaceId/board/mine', name: 'SpaceBoardMine', component: stub },
      { path: '/spaces/:spaceId/board/review', name: 'SpaceBoardReview', component: stub },
      { path: '/spaces/:spaceId/board/publish', name: 'SpaceBoardTaskPublish', component: stub },
      { path: '/spaces/:spaceId/board/tasks/:taskId', name: 'SpaceBoardTaskDetail', component: stub },
    ],
  })
  await router.push(`/spaces/${SPACE_ID}/board`)
  await router.isReady()

  const utils = render(Page, {
    global: { plugins: [createVuetify({ components, directives }), router] },
  })
  // 首页自己不装空间（那是外壳的活），这里补上它，顺序也就跟真的一样。
  await loadBoard(SPACE_ID, true)
  return utils
}

describe('空间首页', () => {
  beforeEach(() => {
    localStorage.clear()
    // 每次给一个**新对象**：store 里空间是个 ref，塞同一个引用进去 Vue 不会认为
    // 它变了，上一个用例算出来的角色就会一直留在缓存里 —— 测试之间于是互相串。
    spaceDetail.mockImplementation(async () => ({ data: { space: { ...SPACE } } }))
    listTasks.mockImplementation(async (params: { approved?: string }) =>
      params?.approved === 'NONE'
        ? { data: { tasks: [PENDING], page: {} } }
        : { data: { tasks: [PUBLISHED, PENDING], page: {} } }
    )
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
    localStorage.clear()
  })

  it('板上只列已上板的题，待审的不混进来', async () => {
    signIn('caisongyang')
    await mount()
    await waitFor(() => expect(document.body.textContent).toContain('已上板的题'))
    expect(document.body.textContent).not.toContain('还在等审的题')
  })

  it('普通成员看不到「等你审」那一块', async () => {
    signIn('someone-else')
    await mount()
    await waitFor(() => expect(document.body.textContent).toContain('数据结构空间'))
    expect(document.body.textContent).not.toContain('在等你审')
  })

  it('管理员看到待审数量，而且那是另一次请求', async () => {
    signIn('caisongyang')
    await mount()
    await waitFor(() => expect(document.body.textContent).toContain('在等你审'))
    expect(listTasks).toHaveBeenCalledWith(expect.objectContaining({ approved: 'NONE' }))
  })
})
