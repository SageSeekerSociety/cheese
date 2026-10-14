// 首屏那份语言包取不回来时，界面退回入口那一门，浏览器里存的那一门不动
// （`i18n/index.ts` 的 `fallBackToEntryLocale`）。这时登录一次：界面这一门不能被
// 当成这个人的选择写回账号——那会把他真正选的那门语言从账号上抹掉，连别的设备一起。
//
// 这份文件要的是「包取不回来」，所以 `./messages/en` 在整份文件里都失败；setup 的
// 预取走导出的那个 `loadLocale`，替它吞掉失败，不然整个文件倒在预取那一步
// （和 `i18n/lazyCatalog.spec.ts` 同一个道理）。最后一条要的是「首屏没取回来、之后
// 取得回来了」，那一条用例自己把包打开（`state.packLands`）。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const setLanguage = vi.fn()
const getCurrentUser = vi.fn()

vi.mock('@/i18n/messages/en', async (importOriginal) => {
  const actual = await importOriginal<{ default: unknown }>()
  return {
    // `packLands` 只有最后一条用例打开：那一条说的是首屏没取回来、这一次取得回来了。
    get default() {
      return state.packLands ? Promise.resolve(actual.default) : Promise.reject(new Error('offline'))
    },
  }
})

// 这份包取不回来是这份文件的前提，取包失败也照实交给调它的人。
const state = vi.hoisted(() => ({ inCase: false, packLands: false }))

vi.mock('@/i18n', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/i18n')>()
  return {
    ...actual,
    async loadLocale(locale: 'zh-CN' | 'en', unattended = false) {
      // setup 的预取在包取不回来时会倒在那一句上，替它吞掉（见上面）；用例里的取包照实
      // 失败——吞掉的话，账号那条换语言会把一次失败当成取回来了，界面就换过去了。
      if (!state.inCase) return actual.loadLocale(locale, unattended).catch(() => {})
      return actual.loadLocale(locale, unattended)
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

beforeEach(() => {
  state.inCase = true
  state.packLands = false
  localStorage.clear()
  setLanguage.mockReset()
  getCurrentUser.mockReset()
  vi.stubGlobal('BroadcastChannel', LoneTab)
  vi.resetModules()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('a first screen whose English pack did not arrive', () => {
  it('does not hand the entry locale to the account at the next sign-in', async () => {
    // 这个浏览器里选过英文，账号上也是英文。
    localStorage.setItem('cheese:locale', 'en')
    localStorage.setItem('accessToken', LIVE_TOKEN)
    localStorage.setItem('user', JSON.stringify({ ...ALICE, language: 'en' }))

    const i18nModule = await import('@/i18n')
    await i18nModule.localeReady
    // 首屏没取回来：界面退回入口那一门，那个人选的还留着。
    expect(i18nModule.default.global.locale.value).toBe('zh-CN')
    expect(i18nModule.storedLocale()).toBe('en')

    const account = (await import('./account')).default
    await account.login(LIVE_TOKEN, { ...ALICE, language: 'en' })
    // 界面是中文只是因为包没取回来，他选的是英文：账号上不能变成中文。
    expect(setLanguage).not.toHaveBeenCalled()
  })

  it('does not hand it to the account when the record names a language it cannot show', async () => {
    // 这个浏览器里选过英文；账号上那条记录是一种这台机器没有的语言（服务端那边留下的）。
    localStorage.setItem('cheese:locale', 'en')
    localStorage.setItem('accessToken', LIVE_TOKEN)
    localStorage.setItem('user', JSON.stringify({ ...ALICE, language: 'en' }))

    const i18nModule = await import('@/i18n')
    await i18nModule.localeReady
    expect(i18nModule.default.global.locale.value).toBe('zh-CN')
    expect(i18nModule.storedLocale()).toBe('en')

    const account = (await import('./account')).default
    await account.login(LIVE_TOKEN, { ...ALICE, language: 'de' })
    // 那条记录说的这门语言这里显示不出来，界面这一门是退回来的：同样不是他选的，
    // 不写回账号。
    expect(setLanguage).not.toHaveBeenCalled()
  })

  it('follows the account once the pack can be fetched after all', async () => {
    // 和第一条同样的开头：这个浏览器里选过英文，账号上也是英文。
    localStorage.setItem('cheese:locale', 'en')
    localStorage.setItem('accessToken', LIVE_TOKEN)
    localStorage.setItem('user', JSON.stringify({ ...ALICE, language: 'en' }))

    const i18nModule = await import('@/i18n')
    await i18nModule.localeReady
    // 首屏那一次没取回来：界面退回入口那一门，那个人选的还留着。
    expect(i18nModule.default.global.locale.value).toBe('zh-CN')
    expect(i18nModule.storedLocale()).toBe('en')

    // 这一次网络通了。
    state.packLands = true

    const account = (await import('./account')).default
    await account.login(LIVE_TOKEN, { ...ALICE, language: 'en' })
    // 账号上这条记录说的是一次取不回来才没显示出来的那一门，取向的那条路（`followLanguage`
    // 的换语言那一支）这次走得通：界面该落回他选的那一门。拦下它（`&& stored === current`
    // 那半条）就把人留在中文上——偏好和账号都说着英文，只有界面是中文。
    await vi.waitFor(() => expect(i18nModule.default.global.locale.value).toBe('en'))
    expect(setLanguage).not.toHaveBeenCalled()
  })
})
