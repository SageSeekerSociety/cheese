// 「账号上那份语言的包还在路上，这个人自己又选了一次」或者「又来了一条更新的记录」
// （换一门／什么都不换）这种次序要看得见，就得把取包停在半路；按住取包要连 i18n
// 模块一起 mock，而 mock
// 住的模块不跟着 `vi.resetModules()`
// 重来——这份文件里从头到尾是同一个 i18n 实例，语言是它上面的状态（`account.language.
// spec.ts` 靠每条测试一个新实例来隔离，所以那两条放在这里，不放进那份）。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const setLanguage = vi.fn()
const getCurrentUser = vi.fn()

// 取包停在半路：`hold` 一装上，下一次取包就等着它被调用。
const state = vi.hoisted(() => ({ hold: null as (() => void) | null, landed: 0 }))
// 谁来取的包（`account.ts` 是这份文件里唯一从外面取包的人），以及它是按「没人在等」
// 那种取法取的没有。
const calls = vi.hoisted(() => ({ packs: [] as Array<['zh-CN' | 'en', boolean]> }))

vi.mock('@/i18n', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/i18n')>()
  return {
    ...actual,
    async loadLocale(locale: 'zh-CN' | 'en', unattended = false) {
      calls.packs.push([locale, unattended])
      if (state.hold) {
        await new Promise<void>((resolve) => {
          state.hold = resolve
        })
      }
      await actual.loadLocale(locale, unattended)
      state.landed += 1
    },
  }
})

vi.mock('@/api/users', () => ({
  UserApi: {
    setLanguage: (...args: unknown[]) => setLanguage(...args),
    getCurrentUser: (...args: unknown[]) => getCurrentUser(...args),
    setTimezone: () => Promise.resolve({}),
  },
}))

// `account.ts` 会 import vuetify-sonner；那条冷启动比这份测试的时限还长。见
// `account.language.spec.ts`。
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

/** A signed-in page whose account keeps `language`, the way `init` finds it after a
 *  reload: a stored token, the user this browser last saw, and the server's answer. */
async function signedInPage(language: 'zh-CN' | 'en') {
  vi.resetModules()
  localStorage.setItem('accessToken', LIVE_TOKEN)
  localStorage.setItem('user', JSON.stringify({ ...ALICE, language: 'en' }))
  getCurrentUser.mockResolvedValue({ user: { ...ALICE, language } })
  const account = (await import('./account')).default
  const i18nModule = await import('@/i18n')
  return {
    account,
    chooseLocale: i18nModule.chooseLocale,
    setLocale: i18nModule.setLocale,
    locale: () => i18nModule.default.global.locale.value,
  }
}

/** Let the held pack come back. */
function release() {
  const resolve = state.hold
  state.hold = null
  resolve?.()
}

beforeEach(() => {
  localStorage.clear()
  // setup 的预取（`src/test/setup-i18n.ts`）也从这个入口取包，那一次不算。
  calls.packs.length = 0
  setLanguage.mockReset()
  setLanguage.mockResolvedValue({ language: 'en' })
  getCurrentUser.mockReset()
  vi.stubGlobal('BroadcastChannel', LoneTab)
})

afterEach(() => {
  vi.unstubAllGlobals()
  // 一条测试要是没走到放手那一步，别把下一条的取包也按在那里。
  state.hold = null
})

describe('the account language comes with its pack', () => {
  it('switches only once that pack is here', async () => {
    const { account, setLocale, locale } = await signedInPage('zh-CN')
    setLocale('en')
    state.hold = () => {}
    await account.init()
    // 包还在路上：这会儿换过去就是一片裸键。
    expect(locale()).toBe('en')
    // 这一趟没人在等结果：它是按那种取法取的，取着的这段时间 `services/staleBuild.ts`
    // 不拿整页刷新去救它——账号这条路上失败了也有落点（留在这门语言上，见 index.ts）。
    expect(calls.packs).toContainEqual(['zh-CN', true])
    release()
    await vi.waitFor(() => expect(locale()).toBe('zh-CN'))
  })

  it('does not override a language picked while that pack is on its way', async () => {
    const { account, chooseLocale, setLocale, locale } = await signedInPage('zh-CN')
    setLocale('en')
    state.hold = () => {}
    state.landed = 0
    await account.init()
    // 账号上那份中文的包还在路上，这个人自己选了一次英文：这次才算数，账号上的
    // 那条记录是旧的。
    await chooseLocale('en')
    await vi.waitFor(() => expect(setLanguage).toHaveBeenCalledWith('en'))
    release()
    await vi.waitFor(() => expect(state.landed).toBeGreaterThan(0))
    // 取回来的中文包不能把他选的换掉。
    expect(locale()).toBe('en')
  })

  it('lets the newest record win over one whose pack is still on its way', async () => {
    const { account, setLocale, locale } = await signedInPage('en')
    setLocale('zh-CN')
    state.hold = () => {}
    state.landed = 0
    await account.init()
    // 记录 A 说英文，包还在路上；接着来了一条更新的记录，说的正是界面现在这一门。
    getCurrentUser.mockResolvedValue({ user: { ...ALICE, language: 'zh-CN' } })
    await account.updateUserInfo()
    release()
    await vi.waitFor(() => expect(state.landed).toBeGreaterThan(0))
    // A 那一次换语言是旧记录说的，不能落地。
    expect(locale()).toBe('zh-CN')
  })

  it('lets a newer record win while that write is still out', async () => {
    const { account, setLocale, locale } = await signedInPage('en')
    setLocale('zh-CN')
    state.hold = () => {}
    state.landed = 0
    await account.init()
    // 记录 A 说英文，包还在路上；接着来了一条语言认不出来的记录（服务端那边一种
    // 这台机器没有的语言），于是它把眼下这一门记上去——那条写要等一会儿才发完。
    // 拦住 A 的不是「有人正把语言记上去」：那一次写也是这条记录发起的，它已经把记录
    // 数推高了（见 `followLanguage` 的 `.then` 比的那两个数）。
    setLanguage.mockReturnValue(new Promise(() => {}))
    getCurrentUser.mockResolvedValue({ user: { ...ALICE, language: 'de' } })
    await account.updateUserInfo()
    release()
    await vi.waitFor(() => expect(state.landed).toBeGreaterThan(0))
    // A 是旧记录说的：它这会儿落地，就把更新那条记录定下的中文换掉了。
    expect(locale()).toBe('zh-CN')
  })

  it('lets a newer record that changes nothing win once that write has gone out', async () => {
    const { account, setLocale, locale } = await signedInPage('en')
    setLocale('zh-CN')
    state.hold = () => {}
    state.landed = 0
    await account.init()
    // 记录 A 说英文，包还在路上。接着来了一条更新的记录，语言认不出来（服务端那边一种
    // 这台机器没有的语言）：它什么都不换，只把眼下这一门记上去——那一次写很快就发完了，
    // 于是「正有人把它记上去」这条拦截也就没了。剩下能拦住 A 的，只有「它是旧记录的
    // 说的」这一条。
    setLanguage.mockResolvedValue({ language: 'zh-CN' })
    getCurrentUser.mockResolvedValue({ user: { ...ALICE, language: 'de' } })
    await account.updateUserInfo()
    await vi.waitFor(() => expect(setLanguage).toHaveBeenCalledWith('zh-CN'))
    await new Promise((resolve) => setTimeout(resolve, 0))
    release()
    await vi.waitFor(() => expect(state.landed).toBeGreaterThan(0))
    // 那条更新的记录已经说了「这会儿是什么就是什么」：A 说的英文不能落地。
    expect(locale()).toBe('zh-CN')
  })
})
