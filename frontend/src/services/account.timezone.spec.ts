// 安静时段按这个人墙上的钟点算，服务端只能从账号上知道他在哪个时区。所以从这个人的
// 角度要成立的是：账号上记的是他正在用的这个浏览器的时区，人换了时区，打开页面就跟
// 过去；已经一致的不再报。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const setTimezone = vi.fn()
const setLanguage = vi.fn()
const getCurrentUser = vi.fn()

vi.mock('@/network/api/users', () => ({
  UserApi: {
    setTimezone: (...args: unknown[]) => setTimezone(...args),
    setLanguage: (...args: unknown[]) => setLanguage(...args),
    getCurrentUser: (...args: unknown[]) => getCurrentUser(...args),
  },
}))

class LoneTab {
  addEventListener() {}
  postMessage() {}
  close() {}
}

const ALICE = {
  id: 1,
  username: 'alice',
  nickname: 'Alice',
  avatarId: 1,
  intro: '',
  question_count: 0,
  answer_count: 0,
  language: 'zh-CN',
}

const browserZone = Intl.DateTimeFormat().resolvedOptions().timeZone

async function freshPage() {
  vi.resetModules()
  return (await import('./account')).default
}

beforeEach(() => {
  localStorage.clear()
  setTimezone.mockReset()
  setTimezone.mockResolvedValue({ data: { timezone: browserZone } })
  setLanguage.mockReset()
  setLanguage.mockResolvedValue({ data: { language: 'zh-CN' } })
  getCurrentUser.mockReset()
  vi.stubGlobal('BroadcastChannel', LoneTab)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('the account keeps the time zone of the browser in use', () => {
  it('an account that never had one gets this browser’s at sign-in', async () => {
    const account = await freshPage()
    await account.login('token', { ...ALICE, timezone: null })
    await vi.waitFor(() => expect(setTimezone).toHaveBeenCalledWith(browserZone))
    await vi.waitFor(() => expect(account.user?.timezone).toBe(browserZone))
  })

  it('a person who moved to another time zone is followed there', async () => {
    const account = await freshPage()
    const elsewhere = browserZone === 'Pacific/Auckland' ? 'Europe/London' : 'Pacific/Auckland'
    await account.login('token', { ...ALICE, timezone: elsewhere })
    await vi.waitFor(() => expect(setTimezone).toHaveBeenCalledWith(browserZone))
  })

  it('an account already on this browser’s time zone is left alone', async () => {
    const account = await freshPage()
    await account.login('token', { ...ALICE, timezone: browserZone })
    expect(setTimezone).not.toHaveBeenCalled()
  })
})
