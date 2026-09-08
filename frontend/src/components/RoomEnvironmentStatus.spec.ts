// 「运行环境还没好」这条状态：说什么、长什么样、住在哪。
//
// 住在哪是这里最要紧的一条。它以前在话题页最顶上，横跨对话和工作面板、把话题标
// 题也挤下去；可它讲的事只跟对话有关——「你现在打的这条，芝士还接不到」。所以
// 它搬到了输入框上沿，而这个位置得有测试钉住：位置不像文案，改错了不会报错，只
// 会在某次重构里悄悄漂回页顶。
import type { Component } from 'vue'
import type { Topic } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import ChatPanel from './ChatPanel.vue'
import RoomEnvironmentStatus from './RoomEnvironmentStatus.vue'

const Status = RoomEnvironmentStatus as unknown as Component
const Panel = ChatPanel as unknown as Component

let vuetify: ReturnType<typeof createVuetify>
/** 后端这一刻回的运行环境状态。 */
let env: Record<string, unknown> = { state: 'preparing', stage: 'project' }

const settle = async () => {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

function topic(): Topic {
  return {
    id: 't1',
    project_id: 'p1',
    parent_id: null,
    title: 't1',
    kind: 'topic',
    status: 'active',
    created_by: 'u',
    created_at: '2026-09-01T00:00:00Z',
    updated_at: '2026-09-01T00:00:00Z',
  } as Topic
}

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  env = { state: 'preparing', stage: 'project' }
  localStorage.setItem('cheesex.me', JSON.stringify({ id: '1', handle: 'me', name: 'me', token: '' }))
  vi.stubGlobal(
    'WebSocket',
    class {
      close() {}
      send() {}
      addEventListener() {}
      removeEventListener() {}
    }
  )
  vi.stubGlobal('fetch', async (url: string) => ({
    ok: true,
    status: 200,
    json: async () => {
      const u = String(url)
      if (u.includes('/environment/rooms/')) return { code: 200, data: env }
      if (u.includes('/members')) return { code: 200, data: { data: [], total: 0 } }
      if (u.includes('/progress')) return { code: 200, data: { items: [], updated_at: null } }
      if (u.includes('/tasks')) return { code: 200, data: { data: [], total: 0 } }
      return { code: 200, data: { data: [], total: 0, has_more: false } }
    },
  }))
})

const mountStatus = () => render(Status, { props: { projectId: 'p1', topicId: 't1' }, global: { plugins: [vuetify] } })

describe('运行环境状态条', () => {
  it('准备中：说清楚发的消息会怎么样，并且有一条在动的线', async () => {
    const { container } = mountStatus()
    await settle()

    expect(container.querySelector('.env__line')?.textContent).toContain('正在准备项目')
    expect(container.querySelector('.env__line')?.textContent).toContain('芝士会继续处理你的消息')
    // 「还在进行」靠这条线表达；它是本条唯一会动的东西。
    expect(container.querySelector('.env__bar')).not.toBeNull()
  })

  it('装工具和备项目是两句话 —— 人等的时候想知道在等什么', async () => {
    env = { state: 'preparing', stage: 'setup' }
    const { container } = mountStatus()
    await settle()

    expect(container.querySelector('.env__line')?.textContent).toContain('正在安装工具')
  })

  it('失败：换成 danger 的样子，说出下一步，并且不再画那条线', async () => {
    env = { state: 'failed' }
    const { container } = mountStatus()
    await settle()

    const box = container.querySelector('.env')
    expect(box?.className).toContain('env--failed')
    expect(container.querySelector('.env__line')?.textContent).toContain('环境准备失败')
    // 失败是唯一需要人动手的状态，所以它得说出手往哪伸。
    expect(container.querySelector('.env__next')?.textContent).toContain('运行环境设置')
    // 停住了就不该还有东西在动。
    expect(container.querySelector('.env__bar')).toBeNull()
  })

  it('总览芝士在重修环境：算「在进行」，不是失败', async () => {
    env = { state: 'failed', recovery_state: 'retrying' }
    const { container } = mountStatus()
    await settle()

    expect(container.querySelector('.env')?.className).not.toContain('env--failed')
    expect(container.querySelector('.env__line')?.textContent).toContain('正在重新启动')
    expect(container.querySelector('.env__bar')).not.toBeNull()
  })

  it('环境好了就整条消失 —— 它是状态，不是留在页面上的一条记录', async () => {
    env = { state: 'ready' }
    const { container } = mountStatus()
    await settle()

    expect(container.querySelector('.env')).toBeNull()
  })

  // 以下三条是 main 上原有的用例，搬进这份新框架里 —— 位置和长相变了，它们钉的
  // 东西一件没变。
  it('日志按字面渲染，不当 HTML 执行', async () => {
    env = { state: 'preparing', stage: 'setup', log: '<script>output</script>' }
    const { container, getByRole } = mountStatus()
    await settle()

    await getByRole('button', { name: '安装日志' }).click()
    await settle()
    expect(container.querySelector('.env__log')?.textContent).toContain('<script>output</script>')
    // 真的成了一个 <script> 节点的话，日志里任何一段带标签的构建输出都能在页面上跑。
    expect(container.querySelector('script')).toBeNull()
  })

  it('自动修复的两种去向各说各的话', async () => {
    env = { state: 'failed', recovery_state: 'requested' }
    const first = mountStatus()
    await settle()
    expect(first.container.querySelector('.env__next')?.textContent).toContain('已交给总览芝士检查')

    env = { state: 'failed', recovery_state: 'needs_help' }
    const second = mountStatus()
    await settle()
    expect(second.container.querySelector('.env__next')?.textContent).toContain('自动处理未能恢复环境')
  })

  it('换个房间就重新拉一次 —— 上一个房间的状态不能留在屏幕上', async () => {
    env = { state: 'failed' }
    const { container, rerender } = mountStatus()
    await settle()
    expect(container.querySelector('.env')?.className).toContain('env--failed')

    env = { state: 'ready' }
    await rerender({ projectId: 'p1', topicId: 'other' })
    await settle()
    expect(container.querySelector('.env')).toBeNull()
  })

  it('日志默认收着，点一下才展开', async () => {
    env = { state: 'failed', log: '+ pnpm install\n  done' }
    const { container, getByRole } = mountStatus()
    await settle()

    expect(container.querySelector('.env__log')).toBeNull()
    await getByRole('button', { name: '安装日志' }).click()
    await settle()
    expect(container.querySelector('.env__log')?.textContent).toContain('pnpm install')
  })
})

describe('它住在哪', () => {
  it('在输入框上沿，不在时间线里 —— 后面来的消息顶不走它', async () => {
    const { container } = render(Panel, {
      props: { topic: topic(), showComposer: true },
      global: { plugins: [vuetify] },
      slots: { 'composer-notice': '<div class="probe-notice">通知</div>' },
    })
    await settle()

    const notice = container.querySelector('.probe-notice')
    const box = container.querySelector('.composer-box')
    const scroll = container.querySelector('[data-testid="chat-scroll"]')
    expect(notice).not.toBeNull()
    expect(box).not.toBeNull()
    // 在输入框之前：DOM 顺序就是屏幕上的上下顺序。
    expect(notice!.compareDocumentPosition(box!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    // 而且不在会滚的那一段里 —— 在里面的话，两条新消息就把它推走了。
    expect(scroll?.contains(notice as Node)).not.toBe(true)
  })
})
