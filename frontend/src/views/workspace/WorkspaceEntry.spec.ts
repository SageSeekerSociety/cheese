/** 进项目的第一屏。
 *
 * `/projects/:id` 自己不显示任何东西，它把人送到一个说得出自己在显示什么的地址。
 * 桌面上那个地址是**看板**：第一眼该答的是「整个项目现在什么在跑、什么在等我」，
 * 而落进大本营答的是「这一个房间里最近说了什么」。
 *
 * 手机上这一层就是话题列表（页面栈里的一层），所以它一步都不跳——跳了就没有"回上
 * 一层"可回。另外，所有已经发出去的 `?topic=` 老链接必须照旧落在它们的话题上。
 */
import type { Component } from 'vue'

import { ref } from 'vue'
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

import WorkspaceEntry from './WorkspaceEntry.vue'

const Entry = WorkspaceEntry as unknown as Component

beforeEach(() => {
  mdAndUp.value = true
  query = {}
  name = 'workspace-project'
  replace.mockReset()
  push.mockReset()
})

function mount() {
  return render(Entry, { props: { projectId: 'p1' } })
}

describe('桌面端', () => {
  it('落在看板上', async () => {
    mount()
    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith({ name: 'workspace-running', params: { projectId: 'p1' } })
    )
  })

  it('不等话题列表到货 —— 目的地和列表无关，等只会换来一屏转圈', () => {
    // 这一屏什么都不画，所以「还在等」这件事必须根本没发生过：跳转在挂载那一刻
    // 就已经发出去了。
    const { container } = mount()
    expect(replace).toHaveBeenCalledTimes(1)
    expect(container.querySelector('.v-progress-circular')).toBeNull()
  })

  it('用的是 replace，不是 push —— 后退不该弹回这个中转地址', async () => {
    mount()
    await waitFor(() => expect(replace).toHaveBeenCalled())
    expect(push).not.toHaveBeenCalled()
  })

  it('已经不在 `/projects/:id` 上了就不跳 —— 跳转结束后视图还活着一小会儿', async () => {
    name = 'workspace-running'
    mount()
    await waitFor(() => expect(replace).not.toHaveBeenCalled())
  })
})

describe('手机端一个字都没变', () => {
  it('不跳，这一层画的就是话题列表', async () => {
    mdAndUp.value = false
    const { queryByTestId } = mount()
    await waitFor(() => expect(queryByTestId('topic-list')).not.toBeNull())
    expect(replace, '跳走就永远看不到列表，也就没有回上一层可回').not.toHaveBeenCalled()
  })
})

describe('老链接', () => {
  it('?topic= 还是跳那个话题，不是跳看板', async () => {
    // 每一条粘贴到聊天里的工作区链接都是这个形状。
    query = { topic: 't-9' }
    mount()
    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith({ name: 'workspace-topic', params: { projectId: 'p1', topicId: 't-9' } })
    )
    expect(replace).toHaveBeenCalledTimes(1)
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
