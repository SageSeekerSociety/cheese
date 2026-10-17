// 界面语言存在账号上：推送和桌面端通知是服务端按收件人的语言写的，服务端只能从账号
// 上知道这个人读哪种语言。所以从这个人的角度要成立的是：在哪儿选的语言，换一台机器、
// 下次登录还是它；登录之前刚在页头选的那个不会被账号上的旧选择盖掉；没选过的人，第一
// 次登录时把他眼前的语言记上去。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const setLanguage = vi.fn()
const getCurrentUser = vi.fn()

vi.mock('@/api/users', () => ({
  UserApi: {
    setLanguage: (...args: unknown[]) => setLanguage(...args),
    getCurrentUser: (...args: unknown[]) => getCurrentUser(...args),
    setTimezone: () => Promise.resolve({}),
  },
}))

// `account.ts` says something on screen when the server ends a sign-in. Nothing
// here takes that path, but importing the module pulls vuetify-sonner in with
// it, and that cold start is enough to run `freshPage`'s import past the 5s test
// timeout. Stubbed, the way `ErrorHandler.spec.ts` stubs it.
vi.mock('vuetify-sonner', () => ({ toast: { error: vi.fn() } }))

// One tab per test: a real channel would carry an earlier test's sign-in to the
// services earlier tests left behind.
class LoneTab {
  addEventListener() {}
  postMessage() {}
  close() {}
}

// An access token that has not expired; only its payload is read.
const LIVE_TOKEN = `header.${btoa(JSON.stringify({ exp: Date.now() / 1000 + 3600 }))}.signature`

const ALICE = {
  id: 1,
  username: 'alice',
  nickname: 'Alice',
  avatarId: 1,
  intro: '',
  question_count: 0,
  answer_count: 0,
}

// A page load: the service and the i18n instance start from what this browser
// remembers, as a fresh tab does.
async function freshPage() {
  vi.resetModules()
  const account = (await import('./account')).default
  // `chooseLocale` is what the language switch on the page calls.
  const { default: i18n, chooseLocale } = await import('@/i18n')
  return { account, chooseLocale, locale: () => i18n.global.locale.value }
}

beforeEach(() => {
  localStorage.clear()
  setLanguage.mockReset()
  setLanguage.mockResolvedValue({ language: 'en' })
  getCurrentUser.mockReset()
  vi.stubGlobal('BroadcastChannel', LoneTab)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('the UI language follows the account', () => {
  it('a language picked while signed in is kept on the account', async () => {
    const { account, chooseLocale, locale } = await freshPage()
    await account.login('token', { ...ALICE, language: 'zh-CN' })
    // `chooseLocale` 取到那份语言包才换过去（见 src/i18n/index.ts），所以要等。
    await chooseLocale('en')
    expect(locale()).toBe('en')
    await vi.waitFor(() => expect(setLanguage).toHaveBeenCalledWith('en'))
  })

  it('signing in on a browser that never picked one shows the language the account keeps', async () => {
    const { account, locale } = await freshPage()
    expect(locale()).not.toBe('zh-CN')
    await account.login('token', { ...ALICE, language: 'zh-CN' })
    // 账号上的语言要等语言包取到才换过去，不先画裸键。
    await vi.waitFor(() => expect(locale()).toBe('zh-CN'))
    expect(setLanguage).not.toHaveBeenCalled()
    // …and the browser remembers it for the signed-out pages too.
    expect(localStorage.getItem('cheese:locale')).toBe('zh-CN')
  })

  it('a language this browser kept from an earlier visit wins at sign-in', async () => {
    localStorage.setItem('cheese:locale', 'en')
    const { account, locale } = await freshPage()
    await account.login('token', { ...ALICE, language: 'zh-CN' })
    expect(locale()).toBe('en')
    await vi.waitFor(() => expect(setLanguage).toHaveBeenCalledWith('en'))
  })

  it('reopening the page while signed in follows a change made on another device', async () => {
    localStorage.setItem('cheese:locale', 'zh-CN')
    localStorage.setItem('accessToken', LIVE_TOKEN)
    localStorage.setItem('user', JSON.stringify({ ...ALICE, language: 'zh-CN' }))
    getCurrentUser.mockResolvedValue({ user: { ...ALICE, language: 'en' } })
    const { account, locale } = await freshPage()
    await account.init()
    await vi.waitFor(() => expect(locale()).toBe('en'))
  })

  it('someone who never picked one has the language in front of them recorded', async () => {
    localStorage.setItem('cheese:locale', 'en')
    const { account, locale } = await freshPage()
    await account.login('token', { ...ALICE, language: null })
    expect(locale()).toBe('en')
    expect(setLanguage).toHaveBeenCalledWith('en')
  })

  it('a language picked on the sign-in page wins over the one the account kept', async () => {
    localStorage.setItem('cheese:locale', 'zh-CN')
    const { account, chooseLocale, locale } = await freshPage()
    await chooseLocale('en')
    expect(setLanguage).not.toHaveBeenCalled()
    await account.login('token', { ...ALICE, language: 'zh-CN' })
    expect(locale()).toBe('en')
    expect(setLanguage).toHaveBeenCalledWith('en')
  })

  it('a refreshed sign-in that answers before the save lands does not undo the pick', async () => {
    let land: () => void = () => {}
    setLanguage.mockImplementation(() => new Promise<void>((resolve) => (land = resolve)))
    const { account, chooseLocale, locale } = await freshPage()
    await account.login('token', { ...ALICE, language: 'zh-CN' })
    // 选中的语言要等语言包取到才落下去（见 src/i18n/index.ts），这次保存就一直挂着。
    await chooseLocale('en')
    await account.login('token', { ...ALICE, language: 'zh-CN' })
    expect(locale()).toBe('en')
    land()
  })

  it('an old record that answers before the save lands does not undo the pick', async () => {
    let land: () => void = () => {}
    setLanguage.mockImplementation(() => new Promise<void>((resolve) => (land = resolve)))
    getCurrentUser.mockResolvedValue({ user: { ...ALICE, language: 'zh-CN' } })
    const { account, chooseLocale, locale } = await freshPage()
    await account.login('token', { ...ALICE, language: 'zh-CN' })
    // 在页头选英文：这一下要写回账号，一直挂着没回来。
    await chooseLocale('en')
    expect(locale()).toBe('en')
    // 保存还没回来，续签那条路（不是新登录，`updateUserInfo`）送来一条保存之前的记录，
    // 说的还是中文。它比这次选择旧：拿它把界面换回去，人看着自己刚选的英文变回中文，
    // 账号上那一次保存回来又要把界面翻过去一遍（`savingLanguage`）。
    await account.updateUserInfo()
    expect(locale()).toBe('en')
    expect(localStorage.getItem('cheese:locale')).toBe('en')
    land()
  })
})
