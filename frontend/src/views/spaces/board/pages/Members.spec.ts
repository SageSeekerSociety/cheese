// 「加入方式」那一列钉三条，都是这一格最容易说错话的地方：
//
// 1. **有记录就写那张码**。接口的成员行带 `inviteCode`，只在核销那一刻写下。
// 2. **没记录写「未知」**，不是空白、也不是「没用过码」。加这一格之前进来的成员
//    谁都没记过，所有者直接加进来的人本来就没用码 —— 这两种在行上长得一模一样。
// 3. **不能拿板上现有的码顶上**。这是最要紧的一条：页面下方「让人进来」那一块就
//    摆着当前可用的码，顺手把它填进每一行会让一条没记过的事实看起来格外确定。
//    所以断言要落在**那一行**上，不能只看整页文本 —— 整页文本里那个码本来就在。
import type { Component } from 'vue'
import type { SpaceInviteCode } from '@/types'

import { defineComponent, h, type Ref } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const listMembers = vi.fn()

vi.mock('@/network/api/spaces', () => ({
  SpacesApi: {
    listMembers: (...a: unknown[]) => listMembers(...a),
  },
}))

vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

// 这一页读的是模块级单例 store（不是 pinia），把整块换掉比让真 store 跑一遍便宜：
// 真 store 的 `space` 要靠 `loadBoard()` 去拉，而这一页的测试只关心名单本身。
//
// ref 必须在工厂**里面**造：`vi.mock` 会被提到文件最前面，外面那些 `const` 那时
// 还没初始化（`vi.fn()` 那种能活下来，`ref(...)` 不行）。造好的几个从被换掉的
// store 上再取回来用 —— 和这一页读的是同一个对象，见 `storeRefs()`。
vi.mock('../store', async () => {
  const { ref } = await import('vue')
  return {
    currentCode: ref(null),
    isOwner: ref(false),
    me: ref({ handle: 'me', name: '我' }),
    space: ref({
      id: 11,
      name: '一块板',
      intro: '',
      owner: { handle: 'boss', name: '板主' },
      // 所有者也是管理员名单里的人（角色只有一处来源），马小雨是另一位管理员。
      admins: [
        { handle: 'boss', name: '板主' },
        { handle: 'maxiaoyu', name: '马小雨' },
      ],
      isCourse: false,
    }),
    loadBoard: vi.fn(async () => {}),
    loadCodes: vi.fn(async () => {}),
  }
})

import * as store from '../store'

import Members from './Members.vue'

const SPACE_ID = 11

/** 上面被换掉的那几个 ref，从 store 上取回来按用例摆状态。 */
function storeRefs() {
  return store as unknown as {
    currentCode: Ref<SpaceInviteCode | null>
    isOwner: Ref<boolean>
  }
}

function member(over: Record<string, unknown> = {}) {
  return {
    userId: 1,
    joinedAt: Date.now(),
    user: { id: 1, username: 'lin', nickname: '林' },
    ...over,
  }
}

function code(over: Partial<SpaceInviteCode> = {}): SpaceInviteCode {
  return {
    id: 7,
    spaceId: SPACE_ID,
    code: 'LIVE2222BB',
    maxUses: 5,
    useCount: 0,
    expiresAt: null,
    createdAt: Date.now(),
    ...over,
  }
}

const Page = defineComponent({ render: () => h(RouterView) })

async function mount() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/spaces/:spaceId/board/members', name: 'Members', component: Members as Component }],
  })
  await router.push(`/spaces/${SPACE_ID}/board/members`)
  await router.isReady()

  render(Page, {
    global: { plugins: [createVuetify({ components, directives }), router] },
  })
  await waitFor(() => expect(rows().length).toBeGreaterThan(0))
}

/** 表体里每一行，按屏幕上的先后。`Map` 而不是对象：handle 是数据，不是键名。 */
function rows(): { who: string; role: string; join: string }[] {
  return Array.from(document.querySelectorAll('tbody tr')).map((tr) => {
    const cells = tr.querySelectorAll('td')
    return {
      who: (cells[0]?.textContent || '').trim(),
      role: (cells[1]?.textContent || '').trim(),
      join: (cells[2]?.textContent || '').trim(),
    }
  })
}

function rowOf(name: string) {
  return rows().find((r) => r.who.includes(name)) ?? null
}

describe('成员页的「加入方式」', () => {
  beforeEach(() => {
    storeRefs().currentCode.value = null
    storeRefs().isOwner.value = false
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('表头就说这一列是什么', async () => {
    listMembers.mockImplementation(async () => ({ data: { members: [member()] } }))
    await mount()

    const heads = Array.from(document.querySelectorAll('thead th')).map((el) => (el.textContent || '').trim())
    expect(heads).toContain('加入方式')
  })

  it('当初是靠哪张码进来的，就把那张码写出来', async () => {
    listMembers.mockImplementation(async () => ({
      data: {
        members: [member({ inviteCode: { id: 3, code: 'ABCD2345EF' } })],
      },
    }))
    await mount()

    expect(rowOf('林')?.join).toBe('ABCD2345EF')
  })

  it('没有记录就写「未知」——不写空白，也不替他说「没用过码」', async () => {
    listMembers.mockImplementation(async () => ({ data: { members: [member()] } }))
    await mount()

    const row = rowOf('林')
    expect(row?.join).toBe('未知')
    // 空白读起来像「还没查」，而这里是一条确定的「没有记录」。
    expect(row?.join).not.toBe('')
  })

  it('板上有张能用着的码，也不拿它填进没有记录的那一行', async () => {
    storeRefs().currentCode.value = code({ code: 'LIVE2222BB' })
    listMembers.mockImplementation(async () => ({
      data: {
        members: [
          member({ userId: 1, user: { id: 1, username: 'lin', nickname: '林' } }),
          // 「有记录」与「没记录」并排，才看得出界面分得开这两种。
          member({
            userId: 2,
            user: { id: 2, username: 'zhou', nickname: '周' },
            inviteCode: { id: 3, code: 'ABCD2345EF' },
          }),
        ],
      },
    }))
    await mount()

    // 当前码在页面下方「让人进来」那一块照常摆着 —— 它该在那儿。
    expect(document.body.textContent).toContain('LIVE2222BB')
    expect(rowOf('林')?.join).toBe('未知')
    expect(rowOf('周')?.join).toBe('ABCD2345EF')
  })
})

// 这一批补的是角色旁那句旁注，以及**所有者那一行的例外**：他不是靠哪张邀请码进来的，
// 所以那格读「建板时就在」，而不是跟别人一样读「未知」。上面那条「不替没记录的人说话」
// 照旧管着其余每一行 —— 所以这里要并排摆三种角色，才看得出只放行了所有者一个。
describe('成员页的角色旁注与所有者的「加入方式」', () => {
  beforeEach(() => {
    storeRefs().currentCode.value = null
    storeRefs().isOwner.value = false
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  /** 三种角色各一行：板主是所有者、马小雨是管理员、林是成员。 */
  function roster() {
    listMembers.mockImplementation(async () => ({
      data: {
        members: [
          member({ userId: 9, user: { id: 9, username: 'boss', nickname: '板主' } }),
          member({ userId: 2, user: { id: 2, username: 'maxiaoyu', nickname: '马小雨' } }),
          member({ userId: 1, user: { id: 1, username: 'lin', nickname: '林' } }),
        ],
      },
    }))
  }

  it('所有者那一行写「建板时就在」——他不是靠码进来的', async () => {
    roster()
    await mount()

    expect(rowOf('板主')?.join).toBe('建板时就在')
  })

  it('其余没有记录的行照旧写「未知」，没跟着一起变', async () => {
    roster()
    await mount()

    expect(rowOf('林')?.join).toBe('未知')
    // 管理员也是所有者授上来的（不是自己用码进来的），那一格同样没有记录可说。
    expect(rowOf('马小雨')?.join).toBe('未知')
  })

  it('角色旁注：所有者「建板的人」、管理员「由所有者授予」，成员没有', async () => {
    roster()
    await mount()

    expect(rowOf('板主')?.role).toContain('建板的人')
    expect(rowOf('马小雨')?.role).toContain('由所有者授予')
    // 成员是缺省角色：那一格只有角色名，没有旁注。
    expect(rowOf('林')?.role).toBe('成员')
  })
})
