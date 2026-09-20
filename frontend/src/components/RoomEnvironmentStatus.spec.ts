// 「运行环境还没好」这条状态：哪几种情况要说话，各说什么。
//
// 最要紧的是**哪几种**。后端会返回 pending / preparing / ready / stopped /
// failed / offline 六种，而这个组件长期只画 preparing 和 failed——于是一个新话题
// 起始的那几十秒（pending）、以及机器离线（offline，芝士永远收不到消息、而且不
// 会自己好）屏幕上一个字都没有。用户报的「哪里都没有」就是这个。
//
// 所以下面每一种状态各有一条：漏掉一种不会报错，只会安静。
import type { Component } from 'vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import RoomEnvironmentStatus from './RoomEnvironmentStatus.vue'

const Status = RoomEnvironmentStatus as unknown as Component

let vuetify: ReturnType<typeof createVuetify>
/** 后端这一刻回的运行环境状态。 */
let env: Record<string, unknown> = { state: 'preparing', stage: 'project' }
/** 后端这会儿打不通（模拟 502）。 */
let down = false

const settle = async () => {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

/** 往前走一个轮询周期。
 *
 *  5 秒是组件自己的间隔，后面那 1.5 秒是传输层的退避：`api.ts` 对 GET 的 502 会
 *  自己重试两次（250ms + 750ms）才最终抛出。不走完这一段，一次「失败」在测试里
 *  根本没有失败完 —— 这也意味着线上真正连着两次读不到，实际经历的是六次请求。 */
const tick = async () => {
  await vi.advanceTimersByTimeAsync(5000)
  await vi.advanceTimersByTimeAsync(1500)
  for (let i = 0; i < 12; i += 1) await Promise.resolve()
}

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  env = { state: 'preparing', stage: 'project' }
  down = false
  vi.useFakeTimers({ shouldAdvanceTime: true })
  localStorage.setItem('user', JSON.stringify({ id: 1, username: 'me', nickname: 'me' }))
  vi.stubGlobal(
    'WebSocket',
    class {
      close() {}
      send() {}
      addEventListener() {}
      removeEventListener() {}
    }
  )
  vi.stubGlobal('fetch', async (url: string) => {
    if (down && String(url).includes('/environment/rooms/')) throw new Error('502')
    return {
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
    }
  })
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
    expect(box?.className).toContain('env--stuck')
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

    expect(container.querySelector('.env')?.className).not.toContain('env--stuck')
    expect(container.querySelector('.env__line')?.textContent).toContain('正在重新启动')
    expect(container.querySelector('.env__bar')).not.toBeNull()
  })

  it('环境好了就整条消失 —— 它是状态，不是留在页面上的一条记录', async () => {
    env = { state: 'ready' }
    const { container } = mountStatus()
    await settle()

    expect(container.querySelector('.env')).toBeNull()
  })

  it('pending：新话题起始的那几十秒也要说话 —— 这是「哪里都没有」的那一种', async () => {
    env = { state: 'pending' }
    const { container } = mountStatus()
    await settle()

    expect(container.querySelector('.env__line')?.textContent).toContain('正在准备运行环境')
    // 它在往前走，所以画那条动的线，且不是「要人管」的样子。
    expect(container.querySelector('.env__bar')).not.toBeNull()
    expect(container.querySelector('.env')?.className).not.toContain('env--stuck')
  })

  it('unbound：还没挑机器就整条不画 —— 没有东西在动，就没有什么要说', async () => {
    env = { state: 'unbound' }
    const { container } = mountStatus()
    await settle()

    expect(container.querySelector('.env')).toBeNull()
  })

  it('offline：机器离线要人去管，而且不会自己好', async () => {
    env = { state: 'offline' }
    const { container } = mountStatus()
    await settle()

    expect(container.querySelector('.env')?.className).toContain('env--stuck')
    expect(container.querySelector('.env__line')?.textContent).toContain('运行设备已离线')
    expect(container.querySelector('.env__next')?.textContent).toContain('重新连接')
    // 停住了就不该还有东西在动。
    expect(container.querySelector('.env__bar')).toBeNull()
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
    expect(container.querySelector('.env')?.className).toContain('env--stuck')

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

describe('拉不到状态的时候', () => {
  it('抖一次不闪 —— 第一次失败保持上一次的样子', async () => {
    env = { state: 'preparing', stage: 'project' }
    const { container } = mountStatus()
    await settle()
    expect(container.querySelector('.env__line')?.textContent).toContain('正在准备项目')

    down = true
    await tick() // 一次失败
    expect(container.querySelector('.env__line')?.textContent).toContain('正在准备项目')
    expect(container.querySelector('.env--unreachable')).toBeNull()
  })

  it('连着两次拉不到就说出来 —— 平台挂了的时候，它是唯一还能说话的地方', async () => {
    env = { state: 'preparing', stage: 'project' }
    const { container } = mountStatus()
    await settle()

    down = true
    await tick()
    await tick() // 第二次
    expect(container.querySelector('.env__line')?.textContent).toContain('暂时读不到运行环境状态')
    // 读不到不是「这个房间坏了」，别画成要人去修的样子。
    expect(container.querySelector('.env')?.className).not.toContain('env--stuck')
    // 也不该还有东西在动。
    expect(container.querySelector('.env__bar')).toBeNull()
  })

  it('恢复之后立刻回到真实状态', async () => {
    env = { state: 'preparing', stage: 'project' }
    const { container } = mountStatus()
    await settle()
    down = true
    await tick()
    await tick()
    expect(container.querySelector('.env--unreachable')).not.toBeNull()

    down = false
    env = { state: 'offline' }
    await tick()
    expect(container.querySelector('.env--unreachable')).toBeNull()
    expect(container.querySelector('.env__line')?.textContent).toContain('运行设备已离线')
  })
})
