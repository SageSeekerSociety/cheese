/** 首页那一格的小点读的那个数：它不能只是「登录那一刻问来的一份快照」。
 *
 *  实际出过的事：小点亮着，点进待办页一条未读也没有——那份未读早就在别处读掉了，
 *  而这一份数从页面打开起就没再问过服务端，谁也没叫它跟上（服务端当时已经是 0）。
 *  这里锁住它什么时候重新问：回到待办页、窗口重新拿到焦点；以及「刚问过就别为同一个
 *  数再问一遍」那一条——焦点会来回切。
 */
import { ref } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { cleanup, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const getUnreadCount = vi.fn()
const loggedIn = ref(true)

vi.mock('@/api/notifications', () => ({ getUnreadNotificationCount: () => getUnreadCount() }))
vi.mock('@/services/account', () => ({ default: { _loggedIn: loggedIn } }))

const Blank = { render: () => null }

const settle = async () => {
  for (let i = 0; i < 4; i += 1) await new Promise((r) => setTimeout(r, 0))
}

/** 这个数在进程内是共享的一份（和首页那一格、待办页读的是同一份），所以每个用例都要
 *  重新 import 一次，不然上一个用例的数和「刚问过」的时刻会跟到下一个用例里。 */
async function mountAt(path: string, { held = false } = {}) {
  vi.resetModules()
  // 首帧这道门在测试里没人推：小点读的数排在它后面，不推就一次都不问（`@/test/firstPaint`）。
  const fp = await import('@/test/firstPaint')
  if (held) fp.holdFirstPaint()
  else await fp.openFirstPaint()
  const { useUnreadNotifications } = await import('../useUnreadNotifications')
  const Probe = {
    setup() {
      const { count } = useUnreadNotifications()
      return { count }
    },
    template: '<span data-count>{{ count }}</span>',
  }
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/:any(.*)*', component: Blank }],
  })
  // 门先攥着的用例要的是「挂载那一刻这一次打开还没落地」这个次序：起始停在 `/`，
  // 跳转由用例自己推（`arrive`）。
  if (!held) await router.push(path)
  const utils = render(Probe, { global: { plugins: [router] } })
  return {
    ...utils,
    router,
    fp,
    arrive: async () => {
      await router.push(path)
    },
    countText: () => utils.container.querySelector('[data-count]')?.textContent ?? '',
  }
}

beforeEach(() => {
  getUnreadCount.mockReset()
  loggedIn.value = true
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('未读小点读的那个数', () => {
  it('冷开就落在待办页上：首帧之前不问，门开了才问一次', async () => {
    getUnreadCount.mockResolvedValue({ count: 2 })
    const { arrive, fp, countText } = await mountAt('/inbox', { held: true })

    // 这一次打开本身：挂载之后跳转才落地，看着像「走进待办页」。
    await arrive()
    await settle()
    expect(getUnreadCount).not.toHaveBeenCalled()

    await fp.releaseFirstPaint()
    await waitFor(() => expect(countText()).toBe('2'))
    expect(getUnreadCount).toHaveBeenCalledTimes(1)
  })

  it('冷开时窗口重新拿到焦点：首帧之前不问，门开了才问', async () => {
    // 开机那一两秒里切走再切回来：`lastAt` 还是 0，节流挡不住这一下，不等门就会
    // 首帧前问一次、门开了再问一次。
    getUnreadCount.mockResolvedValue({ count: 2 })
    const { fp, countText } = await mountAt('/projects/p-1', { held: true })

    window.dispatchEvent(new Event('focus'))
    await settle()
    expect(getUnreadCount).not.toHaveBeenCalled()

    await fp.releaseFirstPaint()
    await waitFor(() => expect(countText()).toBe('2'))
    expect(getUnreadCount).toHaveBeenCalledTimes(1)
  })

  it('请求失败保留已经读到的非零计数', async () => {
    getUnreadCount.mockResolvedValueOnce({ count: 3 })
    const { router, countText } = await mountAt('/projects/p-1')
    await waitFor(() => expect(countText()).toBe('3'))
    getUnreadCount.mockRejectedValueOnce(new Error('offline'))
    await router.push('/inbox')
    await settle()
    expect(getUnreadCount).toHaveBeenCalledTimes(2)
    expect(countText()).toBe('3')
  })

  it('未登录不请求，登录后读计数，登出清零', async () => {
    loggedIn.value = false
    getUnreadCount.mockResolvedValue({ count: 4 })
    const { countText } = await mountAt('/projects/p-1')
    await settle()
    expect(getUnreadCount).not.toHaveBeenCalled()
    expect(countText()).toBe('0')
    loggedIn.value = true
    await waitFor(() => expect(countText()).toBe('4'))
    loggedIn.value = false
    await waitFor(() => expect(countText()).toBe('0'))
    expect(getUnreadCount).toHaveBeenCalledTimes(1)
  })

  it('回到待办页时重新问一次：在别处读掉的未读，小点当场灭', async () => {
    getUnreadCount.mockResolvedValue({ count: 1 })
    const { router, countText } = await mountAt('/projects/p-1')
    await waitFor(() => expect(countText()).toBe('1'))

    // 这一条在别的窗口里被读掉了。
    getUnreadCount.mockResolvedValue({ count: 0 })
    await router.push('/inbox')

    await waitFor(() => expect(countText()).toBe('0'))
  })

  it('窗口重新拿到焦点时也问一次，但刚问过就不为同一个数再问', async () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-01-01T00:00:00Z'))
    getUnreadCount.mockResolvedValue({ count: 0 })
    const { countText } = await mountAt('/projects/p-1')
    await waitFor(() => expect(countText()).toBe('0'))
    expect(getUnreadCount).toHaveBeenCalledTimes(1)

    // 切出去又切回来：几秒钟前刚问过，这一次不问。
    window.dispatchEvent(new Event('focus'))
    await settle()
    expect(getUnreadCount).toHaveBeenCalledTimes(1)

    // 人真的走开了一趟再回来：这时候该问——开着窗口来的一条新未读得让小点亮起来。
    vi.setSystemTime(new Date('2026-01-01T00:01:00Z'))
    getUnreadCount.mockResolvedValue({ count: 2 })
    window.dispatchEvent(new Event('focus'))

    await waitFor(() => expect(countText()).toBe('2'))
    expect(getUnreadCount).toHaveBeenCalledTimes(2)
  })
})
