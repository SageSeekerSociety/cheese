/** LeaveProjectDialog：确认、调接口、把被拒的理由留在弹窗里。
 *
 * 被拒那两条语义：
 *   1. 被拒时弹窗**不关**——人还没退成，「取消」仍然有意义，而后端那句话正是他要的
 *      下一步；它长在弹窗里，不是页面顶上那条和弹窗无关的错误条；
 *   2. 重开弹窗时旧错误**清掉**——否则上一次的拒绝会像还没发生的事一样挂着。
 */
import type { Component } from 'vue'

import { ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { setLocale } from '@/i18n'

const leaveProject = vi.fn()
vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    leaveProject: (...a: unknown[]) => leaveProject(...a),
  }
})

// replace 而不是 push：退出成功后再按回退键不该落回这个项目（见组件里那句注释）。
const replace = vi.fn()
vi.mock('vue-router', () => ({ useRouter: () => ({ replace }), useRoute: () => ({ query: {} }) }))

const refreshMembers = vi.fn()
const refreshProjects = vi.fn()
// 名册上「我」这一行是怎么进来的：随团队进来（team），还是被邀请进来的外部成员（external）。
const members: { user_handle: string; source: string }[] = []
vi.mock('@/stores/workspace', () => ({
  useWorkspaceStore: () => ({ refreshMembers, refreshProjects, members }),
}))

import LeaveProjectDialog from './LeaveProjectDialog.vue'

const Dialog = LeaveProjectDialog as unknown as Component

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  if (!globalThis.visualViewport) {
    ;(globalThis as unknown as { visualViewport: unknown }).visualViewport = {
      width: 1024,
      height: 768,
      offsetLeft: 0,
      offsetTop: 0,
      scale: 1,
      addEventListener() {},
      removeEventListener() {},
      dispatchEvent: () => false,
    }
  }
  if (!('devicePixelRatio' in globalThis)) {
    ;(globalThis as unknown as { devicePixelRatio: number }).devicePixelRatio = 1
  }
})

beforeEach(() => {
  setLocale('zh-CN')
  localStorage.setItem('user', JSON.stringify({ id: 1, username: 'linxia' }))
  members.splice(0, members.length, { user_handle: 'linxia', source: 'team' })
  leaveProject.mockReset().mockResolvedValue({ deleted: true })
  refreshMembers.mockReset().mockResolvedValue(undefined)
  refreshProjects.mockReset().mockResolvedValue(undefined)
  replace.mockReset()
})

/** 挂一个外置开关：弹窗的 open 由它供着，测试能真的关了再开。 */
function mount(projectId = 'p1') {
  const Host = {
    setup() {
      const open = ref(true)
      return { open }
    },
    components: { Dialog },
    template: `<div><button type="button" data-testid="reopen" @click="open = true">打开</button><Dialog v-model="open" project-id="${projectId}" /></div>`,
  }
  return render(Host as unknown as Component, { global: { plugins: [vuetify] } })
}

// These assertions read the Chinese copy; the English rendering is checked in its own case.
beforeEach(() => setLocale('zh-CN'))

describe('LeaveProjectDialog 的被拒语义', () => {
  it('被拒时弹窗不关，理由说在弹窗里——人还没退成', async () => {
    leaveProject.mockRejectedValue(new Error('项目所有者不能退出项目，需要先把项目转让给别人'))
    mount()
    await fireEvent.click(await screen.findByRole('button', { name: '退出' }))
    expect(await screen.findByText(/需要先把项目转让给别人/)).toBeTruthy()
    // 弹窗还开着：确认那一排按钮都还在，人还点得动「取消」。
    expect(await screen.findByRole('button', { name: '退出' })).toBeTruthy()
    expect(await screen.findByRole('button', { name: '取消' })).toBeTruthy()
    expect(replace).not.toHaveBeenCalled()
  })

  it('重开弹窗时旧错误清掉——上一次的拒绝不该还挂着', async () => {
    leaveProject.mockRejectedValue(new Error('你是话题「设计讨论」唯一的 owner，先把话题交给别人'))
    mount()
    await fireEvent.click(await screen.findByRole('button', { name: '退出' }))
    expect(await screen.findByText(/先把话题交给别人/)).toBeTruthy()

    // 取消 = 关掉；reopen = 再点进来。watch(open) 那一行该在这一刻把旧错误清了。
    // （关掉之后 overlay 可能还挂着上一帧的节点，所以「清掉」断言放在**重开之后**
    // ——那才是用户会看见的那一刻。）
    await fireEvent.click(await screen.findByRole('button', { name: '取消' }))
    await fireEvent.click(screen.getByTestId('reopen'))
    expect(await screen.findByRole('button', { name: '退出' })).toBeTruthy()
    expect(screen.queryByText(/先把话题交给别人/)).toBeNull()

    // 这一次的拒绝是新的那句，不是上一次的残留。
    leaveProject.mockRejectedValue(new Error('Not a member'))
    await fireEvent.click(await screen.findByRole('button', { name: '退出' }))
    expect(await screen.findByText(/Not a member/)).toBeTruthy()
    expect(screen.queryByText(/先把话题交给别人/)).toBeNull()
  })

  it('确认框说的是退出这个项目，不是退出团队', () => {
    mount()
    expect(screen.getByText(/退出的是这个项目，不是团队/)).toBeTruthy()
    expect(screen.queryByText(/退出团队/)).toBeNull()
  })

  // 被邀请进来的外部成员（包括别人名下项目里的人）本来就不在什么团队里：跟他说「你在
  // 团队里的身份不变」是一句假话。
  it('不是随团队进来的人，确认框不提团队', () => {
    members.splice(0, members.length, { user_handle: 'linxia', source: 'external' })
    mount()
    expect(screen.getByText(/你将无法查看这个项目/)).toBeTruthy()
    expect(screen.queryByText(/团队/)).toBeNull()
  })

  it('确认之后退出、刷新、回首页；刷新失败也照样走', async () => {
    refreshMembers.mockRejectedValue(new Error('boom'))
    refreshProjects.mockRejectedValue(new Error('boom'))
    mount()
    await fireEvent.click(await screen.findByRole('button', { name: '退出' }))
    await waitFor(() => expect(leaveProject).toHaveBeenCalledWith('p1'))
    await waitFor(() => expect(replace).toHaveBeenCalledWith({ name: 'HomeSpaces' }))
    expect(screen.queryByText(/退出失败/)).toBeNull()
    expect(refreshMembers).toHaveBeenCalled()
    expect(refreshProjects).toHaveBeenCalled()
  })

  it('project-id 原样交给接口——绑错项目等于退错项目', async () => {
    mount('p-other')
    await fireEvent.click(await screen.findByRole('button', { name: '退出' }))
    await waitFor(() => expect(leaveProject).toHaveBeenCalledWith('p-other'))
  })

  it('reads in English under the en locale', async () => {
    setLocale('en')
    mount()
    expect(await screen.findByText('Leave project?')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeTruthy()
    await fireEvent.click(screen.getByRole('button', { name: 'Leave' }))
    await waitFor(() => expect(leaveProject).toHaveBeenCalledWith('p1'))
  })
})
