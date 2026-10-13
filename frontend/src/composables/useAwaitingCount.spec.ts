/** 角标上「待我处理」的件数什么时候跟上。
 *
 *  被动的刷新（窗口拿到焦点、换页面）两次之间至少隔 30 秒；人自己做成了一件会了结
 *  待办的事——在房间里回答了提问、拍了板、采纳了改动、在动态里标了已读——角标当场
 *  重读，不管他是在哪一页做的。这里从网络那一头看：接口真的被叫到，`/awaiting-me`
 *  真的被重新问了一次，角标上的数真的变了。
 */
import { defineComponent, h, ref } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const legacy = vi.hoisted(() => ({ fail: false }))
// 动态（铃铛那一栏）走的是另一套 axios 传输；只替掉它的发送，`NotificationsApi`
// 本身照原样跑。
vi.mock('@/api/axiosHttp', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/axiosHttp')>()),
  axiosRequest: vi.fn(async () => {
    if (legacy.fail) throw new Error('boom')
    return {}
  }),
  axiosMutation: vi.fn(async () => ({})),
}))

import { awaitingCount, useAwaitingCount } from '@/composables/useAwaitingCount'

import { acceptCard, resolveAlert } from '@/api'
import { postChatMessage } from '@/api/messages'
import { NotificationsApi } from '@/api/notifications'
import { openFirstPaint } from '@/test/firstPaint'

type Pending = { resolve: (items: unknown[]) => void }

/** `/awaiting-me` 的每一次读排成队，测试决定每一次什么时候、带着几件回来。 */
let reads: Pending[] = []
let writeFails = false

function answer(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(status === 200 ? { code: 200, data: body } : { message: 'no' }), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function items(n: number): unknown[] {
  return Array.from({ length: n }, (_, i) => ({ projectId: 'p1', alertId: i }))
}

beforeEach(() => {
  reads = []
  writeFails = false
  legacy.fail = false
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: RequestInit) => {
      if (url.endsWith('/awaiting-me')) {
        return new Promise<Response>((resolve) => {
          reads.push({ resolve: (list) => resolve(answer({ data: list, total: list.length })) })
        })
      }
      if ((init?.method ?? 'GET') !== 'GET') return Promise.resolve(writeFails ? answer(null, 409) : answer({}))
      return Promise.resolve(answer(null, 404))
    })
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
})

/** 挂上 App 里取角标数的那一处，等第一次读回来，角标上是 `n`。 */
async function mountBadge(n: number) {
  // 角标的第一遍排在「首帧画完」那道门后面（`lib/afterFirstPaint.ts`）：单测里没有
  // `main.ts`，也不会真跳转，没人推门就一次都不发。这几条用例问的是门开之后的行为
  // （当场重读、节流、晚到的旧数），所以先推开门再挂。
  await openFirstPaint()
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: { render: () => null } }],
  })
  const Badge = defineComponent({
    setup() {
      useAwaitingCount(ref(true))
      return () => h('div')
    },
  })
  const utils = render(Badge, { global: { plugins: [router] } })
  await waitFor(() => expect(reads).toHaveLength(1))
  reads[0].resolve(items(n))
  await waitFor(() => expect(awaitingCount().value).toBe(n))
  return utils
}

describe('人做成了一件会了结待办的事，角标当场重读', () => {
  it.each([
    ['在房间里回答提问', () => postChatMessage('room-1', { content: '用方案 A', request_id: 'r1' })],
    ['在待办页拍板', () => resolveAlert(7, '先灰度')],
    ['采纳一份改动', () => acceptCard('card-1', 'andy', null)],
    ['在动态里把变更提醒标为已读', () => NotificationsApi.updateStatus(3, true)],
  ])('%s：不等 30 秒', async (_what, act) => {
    const utils = await mountBadge(2)

    // 第一次读刚回来，离下一次被动刷新还有 30 秒。
    await act()
    await waitFor(() => expect(reads).toHaveLength(2))
    reads[1].resolve(items(1))
    await waitFor(() => expect(awaitingCount().value).toBe(1))
    utils.unmount()
  })

  it('写没成功就不重读：什么都没变', async () => {
    const utils = await mountBadge(2)
    writeFails = true
    await expect(postChatMessage('room-1', { content: '好', request_id: 'r2' })).rejects.toThrow()
    legacy.fail = true
    await expect(NotificationsApi.updateStatus(3, true)).rejects.toThrow()
    await Promise.resolve()
    expect(reads).toHaveLength(1)
    utils.unmount()
  })

  it('人没做什么时（窗口重新拿到焦点）照旧压着，不是每次都去问', async () => {
    const utils = await mountBadge(2)
    window.dispatchEvent(new Event('focus'))
    await Promise.resolve()
    expect(reads).toHaveLength(1)
    utils.unmount()
  })
})

describe('连着两下操作，两次读回来的先后颠倒', () => {
  it('只认后发出去的那一次：早发的晚到不盖掉新数', async () => {
    const utils = await mountBadge(3)

    await resolveAlert(1, '是')
    await waitFor(() => expect(reads).toHaveLength(2))
    await resolveAlert(2, '否')
    await waitFor(() => expect(reads).toHaveLength(3))

    // 后发的先回来：两件都了结了，剩 1。
    reads[2].resolve(items(1))
    await waitFor(() => expect(awaitingCount().value).toBe(1))
    // 先发的那次这时才回来，带着只了结了第一件时的 2。
    reads[1].resolve(items(2))
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(awaitingCount().value).toBe(1)
    utils.unmount()
  })
})
