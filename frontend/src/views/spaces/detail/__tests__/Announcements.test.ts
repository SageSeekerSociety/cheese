// 老树这一页（`/spaces/:id/announcements`，第六批没删它，它还在各自地址上服务）
// 与公告元素的关系只有一条值得钉：**编辑是整条替换的**。
//
// `spaceStore.updateAnnouncement(index, …)` 按下标把那一格换成弹窗提交上来的新对象，
// 弹窗里没有的字段就跟着一起没了。所以「在新题目板置顶 → 来这边改一次标题 → 置顶
// 没了」是真能走到的路，而它在界面上不报错、只是悄悄少了一个字段。
//
// 这条用例断言的是 **PATCH 出去的那份 JSON**，不是屏幕：屏幕上什么都不会变。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const spaceDetail = vi.fn()
const spaceUpdate = vi.fn()

vi.mock('@/network/api/spaces', () => ({
  SpacesApi: {
    detail: (...args: unknown[]) => spaceDetail(...args),
    update: (...args: unknown[]) => spaceUpdate(...args),
  },
}))

vi.mock('vue-i18n', () => ({ useI18n: () => ({ t: (key: string) => key }) }))
vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

// 这一页只在读一条公告时碰 dialog，这条用例不走那条路。
vi.mock('@/plugins/dialog', () => ({
  useDialog: () => ({ custom: vi.fn(), confirm: () => ({ wait: async () => true }) }),
}))

// 编辑弹窗里挂着真编辑器（tiptap），这里不替身它：替身只能是一个 `vi.mock` 出来的
// 模块，而 @vue/test-utils 遍历组件时会探 `__isTeleport` / `__isKeepAlive` /
// `__isScriptSetup` 三个属性，探针打到 vitest 的模块代理上会直接抛出去（还是在
// watcher 回调里，报成未处理的拒绝）。真编辑器在 jsdom 里渲染得动，这条用例又不
// 碰正文，就用真的。

// 页头那颗面包屑要老树的路由 meta，替身只留它的两个插槽 —— 「+」那颗发公告的按钮
// 在 `#actions` 里，漏掉它这条用例就点不到。
vi.mock('@/components/common/PageHeader.vue', async () => {
  const { defineComponent: dc, h: hh } = await import('vue')
  return {
    default: dc({
      name: 'PageHeaderStub',
      setup:
        (_, { slots }) =>
        () =>
          hh('div', {}, [slots.actions?.(), slots.default?.()]),
    }),
  }
})

vi.mock('@/services/account', () => ({
  default: { _user: { value: { id: 4, username: 'caisongyang', nickname: '蔡松洋' } } },
}))

import Announcements from '../Announcements.vue'

import { useSpaceStore } from '@/stores/space'

const SPACE_ID = 647

/** 一条**置顶的**公告 —— 这一页要证明的就是编辑之后它还是置顶的。 */
const ANNOUNCEMENTS = [
  {
    title: '置顶的公告',
    content: '<p>正文</p>',
    createdAt: Date.UTC(2026, 8, 1),
    updatedAt: Date.UTC(2026, 8, 1),
    publisher: '蔡松洋',
    pinned: true,
  },
]

function space(announcements: unknown[]) {
  return {
    id: SPACE_ID,
    name: '数据结构空间',
    intro: '',
    avatarId: null,
    admins: [{ user: { id: 4, username: 'caisongyang', nickname: '蔡松洋' }, role: 'OWNER' }],
    announcements: JSON.stringify(announcements),
    taskTemplates: '[]',
    classificationTopics: [],
    visibleTaskLimit: null,
  }
}

/** 弹窗里那颗「发布 / 保存」—— i18n 被打桩成返回 key，认这个 key 就行。 */
function submitButton(): Element | null {
  return (
    Array.from(document.querySelectorAll('button')).find((b) => b.textContent?.includes('spaces.detail.publish')) ??
    null
  )
}

async function mountPage() {
  const store = useSpaceStore()
  await store.fetchSpace(SPACE_ID)
  return render(Announcements, {
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  spaceDetail.mockReset().mockImplementation(async () => ({ data: { space: space(ANNOUNCEMENTS) } }))
  spaceUpdate.mockReset().mockImplementation(async () => ({ data: { space: space(ANNOUNCEMENTS) } }))
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

// Vuetify 的遮罩（那个编辑弹窗）一打开就要读这两个，jsdom 两个都没有。
beforeAll(() => {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    scale: 1,
    addEventListener: () => {},
    removeEventListener: () => {},
  })
})

describe('老树的公告页', () => {
  it('编辑一条置顶的公告，置顶不会跟着丢', async () => {
    await mountPage()
    await waitFor(() => expect(document.body.textContent).toContain('置顶的公告'))

    // 铅笔在卡片上 —— 弹窗里没有置顶这一项，编辑也不该动它。
    const edit = document.querySelector('.mdi-pencil')?.closest('button')
    expect(edit).not.toBeNull()
    await fireEvent.click(edit!)

    await waitFor(() => expect(submitButton()).not.toBeNull())
    await fireEvent.click(submitButton()!)

    await waitFor(() => expect(spaceUpdate).toHaveBeenCalled())
    const body = spaceUpdate.mock.calls[0][1] as { announcements: string }
    const written = JSON.parse(body.announcements) as { title: string; pinned?: boolean }[]

    expect(written).toHaveLength(1)
    expect(written[0].title).toBe('置顶的公告')
    expect(written[0].pinned).toBe(true)
  })

  it('发一条新的，没有置顶那回事 —— 这一页也发不了置顶', async () => {
    await mountPage()
    await waitFor(() => expect(document.body.textContent).toContain('置顶的公告'))

    // 页头那颗「+」。
    const create = document.querySelector('button .mdi-plus')?.closest('button')
    expect(create).not.toBeNull()
    await fireEvent.click(create!)

    await waitFor(() => expect(submitButton()).not.toBeNull())
    await fireEvent.click(submitButton()!)

    await waitFor(() => expect(spaceUpdate).toHaveBeenCalled())
    const body = spaceUpdate.mock.calls[0][1] as { announcements: string }
    const written = JSON.parse(body.announcements) as { pinned?: boolean }[]

    expect(written).toHaveLength(2)
    // 新加的那一条不带 `pinned` 这一格 —— 老页面没有置顶开关，别凭空写一个 false 进去。
    expect(written[1].pinned).toBeUndefined()
    // 原来那条也一个字没动。
    expect(written[0].pinned).toBe(true)
  })
})
