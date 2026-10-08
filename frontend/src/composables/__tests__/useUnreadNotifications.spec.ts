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

vi.mock('@/network/api/notifications', () => ({
  NotificationsApi: { getUnreadCount: () => getUnreadCount() },
}))
vi.mock('@/services/account', () => ({ default: { _loggedIn: loggedIn } }))

const Blank = { render: () => null }

const settle = async () => {
  for (let i = 0; i < 4; i += 1) await new Promise((r) => setTimeout(r, 0))
}

/** 这个数在进程内是共享的一份（和首页那一格、待办页读的是同一份），所以每个用例都要
 *  重新 import 一次，不然上一个用例的数和「刚问过」的时刻会跟到下一个用例里。 */
async function mountAt(path: string) {
  vi.resetModules()
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
  await router.push(path)
  const utils = render(Probe, { global: { plugins: [router] } })
  return {
    ...utils,
    router,
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
  it('回到待办页时重新问一次：在别处读掉的未读，小点当场灭', async () => {
    getUnreadCount.mockResolvedValue({ data: { count: 1 } })
    const { router, countText } = await mountAt('/projects/p-1')
    await waitFor(() => expect(countText()).toBe('1'))

    // 这一条在别的窗口里被读掉了。
    getUnreadCount.mockResolvedValue({ data: { count: 0 } })
    await router.push('/inbox')

    await waitFor(() => expect(countText()).toBe('0'))
  })

  it('窗口重新拿到焦点时也问一次，但刚问过就不为同一个数再问', async () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-01-01T00:00:00Z'))
    getUnreadCount.mockResolvedValue({ data: { count: 0 } })
    const { countText } = await mountAt('/projects/p-1')
    await waitFor(() => expect(countText()).toBe('0'))
    expect(getUnreadCount).toHaveBeenCalledTimes(1)

    // 切出去又切回来：几秒钟前刚问过，这一次不问。
    window.dispatchEvent(new Event('focus'))
    await settle()
    expect(getUnreadCount).toHaveBeenCalledTimes(1)

    // 人真的走开了一趟再回来：这时候该问——开着窗口来的一条新未读得让小点亮起来。
    vi.setSystemTime(new Date('2026-01-01T00:01:00Z'))
    getUnreadCount.mockResolvedValue({ data: { count: 2 } })
    window.dispatchEvent(new Event('focus'))

    await waitFor(() => expect(countText()).toBe('2'))
    expect(getUnreadCount).toHaveBeenCalledTimes(2)
  })
})
