/** 待我处理那颗角标读的那个数：它排在首帧之后，不该跟开页那一瞬间抢。
 *
 *  实际出过的事：冷开落在深链上（刷新、从别处点进来）时，这一次跳转是挂载之后才落地
 *  的，路由监听看着像「人换了一页」，于是在首帧之前把这个数读了一遍，门开了又读一遍
 *  ——一页多发一个请求，而且那一次正好挤在开页那一批里。这里锁住首帧之前一次都不读、
 *  门开了只读一次；顺带把「换页问一次」那两条规矩锁住（离开待办页不受节流，别处之间
 *  来回不重复问）。
 */
import { computed, ref } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { cleanup, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const listAwaitingMe = vi.fn()
const loggedIn = ref(true)

vi.mock('@/api', () => ({ listAwaitingMe: () => listAwaitingMe() }))

const Blank = { render: () => null }

const settle = async () => {
  for (let i = 0; i < 4; i += 1) await new Promise((r) => setTimeout(r, 0))
}

/** 挂一个只看角标的探针。`open: false` 表示门还关着，用例自己决定什么时候推门。
 *
 *  这个数在进程内是共享的一份（首页那一格和桌面 rail 读的是同一份），所以每个用例都要
 *  重新 import 一次，不然上一个用例的数和「刚问过」的时刻会跟到下一个用例里。
 */
async function mountProbe({ open = true } = {}) {
  vi.resetModules()
  // 首帧这道门在测试里没人推：角标排在它后面，不推就一次都不读（`@/test/firstPaint`）。
  const fp = await import('@/test/firstPaint')
  if (open) await fp.openFirstPaint()
  else fp.holdFirstPaint()
  const { useAwaitingCount } = await import('../useAwaitingCount')
  const Probe = {
    setup() {
      const count = useAwaitingCount(computed(() => loggedIn.value))
      return { count }
    },
    template: '<span data-count>{{ count }}</span>',
  }
  // 起始位置停在 `/`：`main.ts` 就是这样——挂载那一刻这一次打开还没落地，跳转是之后才
  // 确认的。深链那一条要的正是这个次序。
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/:any(.*)*', component: Blank }],
  })
  const utils = render(Probe, { global: { plugins: [router] } })
  return {
    ...utils,
    router,
    fp,
    countText: () => utils.container.querySelector('[data-count]')?.textContent ?? '',
  }
}

beforeEach(() => {
  listAwaitingMe.mockReset()
  loggedIn.value = true
})

afterEach(() => {
  cleanup()
})

describe('待我处理那颗角标读的数', () => {
  it('冷开落在深链上：首帧之前一次都不读，门开了只读一次', async () => {
    listAwaitingMe.mockResolvedValue({ data: [{ projectId: 'p-1' }] })
    const { router, fp, countText } = await mountProbe({ open: false })

    // 这一次打开本身：挂载之后跳转才落地，看着像一次换页。
    await router.push('/projects/p-1')
    await settle()
    expect(listAwaitingMe).not.toHaveBeenCalled()

    await fp.releaseFirstPaint()
    await waitFor(() => expect(listAwaitingMe).toHaveBeenCalledTimes(1))
    expect(countText()).toBe('1')
  })

  it('首帧之后离开待办页：当场再读一次，不受节流', async () => {
    listAwaitingMe.mockResolvedValue({ data: [] })
    const { router } = await mountProbe()
    await waitFor(() => expect(listAwaitingMe).toHaveBeenCalledTimes(1))

    // 刚读过，去待办页这一次被节流挡住。
    await router.push('/inbox')
    await settle()
    expect(listAwaitingMe).toHaveBeenCalledTimes(1)

    // 从待办页离开那一下不受节流：人刚在那儿处理完事情，数要立刻跟上。
    listAwaitingMe.mockResolvedValue({ data: [{ projectId: 'p-1' }] })
    await router.push('/projects/p-1')
    await waitFor(() => expect(listAwaitingMe).toHaveBeenCalledTimes(2))
  })

  it('未登录不读，登出把数清掉', async () => {
    loggedIn.value = false
    listAwaitingMe.mockResolvedValue({ data: [{ projectId: 'p-1' }] })
    const { countText } = await mountProbe()

    await settle()
    expect(listAwaitingMe).not.toHaveBeenCalled()
    expect(countText()).toBe('0')

    loggedIn.value = true
    await waitFor(() => expect(countText()).toBe('1'))

    loggedIn.value = false
    await waitFor(() => expect(countText()).toBe('0'))
    expect(listAwaitingMe).toHaveBeenCalledTimes(1)
  })
})
