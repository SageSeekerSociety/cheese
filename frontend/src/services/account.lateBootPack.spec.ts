// 首屏那份语言包过了期限才到（`i18n/index.ts` 的 `awaitBootPack`）：界面已经退回入口那一门，
// 包之后到了本该补上。可这期间界面这一门要是已经被定在别的哪一门上了，就不能补——「定下」留
// 下的痕迹有两处：人自己选了，写了浏览器偏好（`i18n/bootPack.spec.ts` 里那条）；或者账号上那
// 条记录替界面定了这一门，而偏好里什么都没有（`followLanguage` 那一支不换也不写偏好）。这一
// 份管的是后一种，四条路各一条用例：账号上说的正是界面上这一门；账号上那条记录说的是一种这里
// 显示不出来的语言，于是界面上这一门被写回账号（也成了账号上那一份）；账号上说的和浏览器偏好
// 都没有时界面本来那一门（浏览器的英文），补上去正是回到账号说的那一门，不该拦；账号定下的那
// 一门在包到之前随会话一起没了（人退出登录），那是「某个人」定下的，会话没了就不再算数，界面该
// 退回浏览器那一门。
//
// 迟到的包用一条自己造的 promise 来演（同 `i18n/bootPack.spec.ts`）：`awaitBootPack` 迟早由谁定
// 跟这条 promise 从哪来无关，造的那条比真那份包好按。每条用例一份新的 i18n（`vi.resetModules`）：
// 「界面这一门已经定下」是记在模块里的，会一直成立到这一页结束，同一份实例上第二条用例看不出
// 区别——真那份包是现取的，重来一次很快。
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getCurrentUser = vi.fn()
const setLanguage = vi.fn()

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

/** 一次冷打开：这个浏览器里没选过语言（偏好空着），界面这一门本来跟着浏览器的语言——en，
 *  入口那一门是中文，两者不同才看得出翻没翻。 */
async function signedOutBrowserInEnglish() {
  vi.resetModules()
  localStorage.clear()
  const i18n = await import('@/i18n')
  await i18n.localeReady
  expect(i18n.default.global.locale.value).toBe('en')
  const account = (await import('./account')).default
  return { ...i18n, account }
}

/** 首屏那一份还在路上、期限先到：界面退回入口那一门，挂载不等了。返回的那一下调用即是
 *  「包到了」。 */
async function fallBackBeforeThePack(i18n: Awaited<ReturnType<typeof signedOutBrowserInEnglish>>) {
  let land: (() => void) | undefined
  const pack = new Promise<void>((resolve) => {
    land = resolve
  })
  await i18n.awaitBootPack(pack, 20)
  expect(i18n.default.global.locale.value).toBe('zh-CN')
  return () => land?.()
}

beforeEach(() => {
  setLanguage.mockReset()
  setLanguage.mockResolvedValue({})
  getCurrentUser.mockReset()
  vi.stubGlobal('BroadcastChannel', LoneTab)
})

describe('a first screen whose pack landed after the deadline', () => {
  it('catches up when the account settled on the language that pack restores', async () => {
    const i18n = await signedOutBrowserInEnglish()

    // 账号上那条记录说的正是界面上这一门（浏览器的英文）：不换，也不写偏好。这一步在前
    // 面——记录那份回来的路上没有那一块语言包，比它先到。
    await i18n.account.login(LIVE_TOKEN, { ...ALICE, language: 'en' })
    expect(setLanguage).not.toHaveBeenCalled()
    expect(i18n.storedLocale()).toBeNull()

    // 期限先到：界面退回入口那一门。这一门不是账号定下的那一门，可账号定下的正是这份包
    // 要恢复的那一门，补上去就是回到账号说的那一门——整场停在中文才是走岔了。
    const land = await fallBackBeforeThePack(i18n)
    land()
    await new Promise((resolve) => setTimeout(resolve, 30))
    expect(i18n.default.global.locale.value).toBe('en')
  })

  it('leaves the language the account settled on where it is', async () => {
    const i18n = await signedOutBrowserInEnglish()
    const land = await fallBackBeforeThePack(i18n)

    // 账号上那条记录说的正是退回来的这一门：它不换语言，也不写偏好——界面这一门是它
    // 定下的，可偏好里看不出来。
    await i18n.account.login(LIVE_TOKEN, { ...ALICE, language: 'zh-CN' })
    expect(setLanguage).not.toHaveBeenCalled()
    expect(i18n.storedLocale()).toBeNull()

    // 包到了：界面这一门是账号上那份定的，不能翻回浏览器语言的英文。
    land()
    await new Promise((resolve) => setTimeout(resolve, 30))
    expect(i18n.default.global.locale.value).toBe('zh-CN')
  })

  it('leaves it where it is when the account had none to show and took the one on screen', async () => {
    const i18n = await signedOutBrowserInEnglish()
    const land = await fallBackBeforeThePack(i18n)

    // 账号上那条记录说的是一种这里显示不出来的语言（服务端那边留下的）：它管不了要哪
    // 一门，于是界面这一门被写回账号——账号上那一份从此就是中文，同样不留偏好。
    await i18n.account.login(LIVE_TOKEN, { ...ALICE, language: 'de' })
    expect(setLanguage).toHaveBeenCalledWith('zh-CN')

    // 包到了：界面这一门刚写回账号，不能翻回浏览器的英文——下一次打开账号上那份就
    // 是中文，翻过去只会让这一次和下一次对不上。
    land()
    await new Promise((resolve) => setTimeout(resolve, 30))
    expect(i18n.default.global.locale.value).toBe('zh-CN')
  })

  it('gives the interface back to the browser language when the session ends before the pack lands', async () => {
    const i18n = await signedOutBrowserInEnglish()
    const land = await fallBackBeforeThePack(i18n)

    // 同上一条：账号上那条记录说的正是退回来的这一门，界面这一门是它定下的，偏好里不
    // 留痕。
    await i18n.account.login(LIVE_TOKEN, { ...ALICE, language: 'zh-CN' })
    expect(i18n.storedLocale()).toBeNull()

    // 人退出登录。这一门是「那个人」定下的，会话没了就不该再拦着——留在中文的话，一个
    // 浏览器是英文、又没选过语言的人，整场都停在中文，刷新一下才是英文。
    await i18n.account.logout()

    // 包到了：偏好里没记过语言，界面这一门退回浏览器那一门。
    land()
    await new Promise((resolve) => setTimeout(resolve, 30))
    expect(i18n.default.global.locale.value).toBe('en')
  })
})
