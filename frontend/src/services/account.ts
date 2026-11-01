import type { Locale } from '@/i18n'
import type { User } from '@/types/users'

import { computed, ref } from 'vue'

import { UserApi } from '@/api/users'
import i18n, {
  forgetAccountLocaleSettled,
  isLocale,
  loadLocale,
  noteAccountLocaleSettled,
  onLocaleChosen,
  setLocale,
  storedLocale,
  t,
} from '@/i18n'
import { storageGet, storageGetJson, storageRemove, storageSet, storageSetJson } from '@/lib/browserStorage'
import { clearComposerDrafts } from '@/lib/composerDrafts'
import { forgetFeedbackDraft } from '@/lib/feedbackDraft'
import { identityChanges } from '@/lib/identity'
import { resetRoomLink } from '@/lib/roomLink'
import { announceSignIn, announceSignOut, onSessionEvent, refreshSession } from '@/lib/session'
import { forgetWindows } from '@/lib/siteWindow'
import { toast } from '@/lib/toast'
import { queryClient } from '@/query/client'
import { forgetPersistedQueries } from '@/query/persist'
import { forgetRoomSnapshots } from '@/query/snapshot'
import { disablePush } from '@/services/webPush'
import { resetFeedbackCaches } from '@/stores/feedback'

// 令牌快到期时也当过期处理：留一点余量，免得请求刚发出去令牌就死在路上。
const EXPIRY_SKEW_MS = 30_000

/**
 * 这个 JWT 是不是已经（或马上就要）过期。
 *
 * 解不出来的令牌一律当过期——宁可多续签一次，也不要拿着一个我们读不懂的东西
 * 去打认证接口换 401。
 */
export function isTokenExpired(token: string, now: number = Date.now()): boolean {
  try {
    const payload = token.split('.')[1]
    if (!payload) return true
    const json = atob(payload.replace(/-/g, '+').replace(/_/g, '/'))
    const exp = JSON.parse(json)?.exp
    if (typeof exp !== 'number') return true
    return exp * 1000 - EXPIRY_SKEW_MS <= now
  } catch {
    return true
  }
}

//: service worker 里那份 API 读缓存的名字（vite.config.ts 的 `cheese-api-get`）。
const API_CACHE = 'cheese-api-get'

/** localStorage 里存着的上一个人是谁 —— 内存里那份还没恢复时的退路。 */
function storedUserId(): number | undefined {
  const id = storageGetJson<User>('user')?.id
  return typeof id === 'number' ? id : undefined
}

/**
 * 换人登录就把上一个人的缓存丢掉。
 *
 * 这几份缓存都装着上一个人读过的东西，而它们都不按人分：
 *
 * - **service worker 的 API 读缓存**（vite.config.ts 的 `cheese-api-get`）按 URL
 *   建键，请求头不进键，后端也没发 `Vary` —— 所以 `GET /api/projects` 全浏览器只
 *   有一份。它是 NetworkFirst、五秒拿不到响应就回退缓存，于是一次慢请求会把上一
 *   个人的数据画到这个人屏幕上。
 * - **服务器数据的缓存**（query/client）住在内存里，项目清单还在本标签页存了一份：
 *   下一个人打开总览会先看到上一个人的项目名，然后才被后台刷新盖掉 —— 那一眼已经
 *   泄露了。
 * - **反馈那三份**（stores/feedback.ts 的公开列表 / 我的反馈 / 详情）也都是「同一
 *   台机器上的下一个人会先看到」的那种：其中「我的反馈」和详情是**按人**的，公开
 *   列表虽然不是私密数据，但它记着这个人刚翻过哪一页。
 *
 * 它们本来都只在退出登录时清（`logout`），而危险的那一下不是退出，是**换人登录**：
 * 上一个人关掉标签页就走了、没点退出，下一个人登进来时全都还在。
 *
 * 同一个人不清：续签令牌也会走到这里（`adopt`），每小时清一次等于这些缓存从来
 * 不存在。所以判据是**身份变了**，不是「又登了一次」。
 *
 * 认不出新身份时（OAuth 回调只给令牌，用户信息随后才拉）当作换了人：那条路径只在
 * 一次全新的登录里走到，宁可多清一次。
 */
/**
 * 这个人看得到的服务器数据，和这个页面的房间连接（连着的是上一个人看着的那些房间）。
 *
 * 缓存里每一份都退回「没读过」：正在读的作废，读到的扔掉。还有地方看着的那几份不从
 * 缓存里拿走 —— 拿走了，看着它的那一处就连在一份谁也不再更新的数据上，回到同一个项目
 * 时既不重读、也换不掉上一个人那份。新的人的令牌到手后，它们再读一次（`readAsNewPerson`）。
 */
function forgetServerData(): void {
  for (const query of queryClient.getQueryCache().getAll()) {
    if (query.observers.length) query.reset()
    else queryClient.getQueryCache().remove(query)
  }
  forgetPersistedQueries()
  forgetRoomSnapshots()
  // 现场那一窗不在 TanStack 里，得自己和上面几份一起放掉：上一个人读过的现场
  // 不能留在下一个人第一帧上。
  forgetWindows()
  resetRoomLink()
}

/** 换了人、令牌也到手了：屏幕上正看着的那几份按这个人的身份读一次。 */
function readAsNewPerson(): void {
  void queryClient.refetchQueries({ type: 'active' })
}

export function dropCachesIfSomeoneElseLogsIn(previous: number | undefined, next: number | undefined): boolean {
  if (previous !== undefined && next !== undefined && previous === next) return false
  forgetServerData()
  // 反馈那三份：不按人分，而且其中两份装的就是「按人」的东西。
  resetFeedbackCaches()
  // 输入框草稿也带着上一个人的话（lib/composerDrafts.ts），而且它的键里只有话题
  // id——话题是全站共享的，不清就等于把上一个人的半句话递给下一个人看。
  clearComposerDrafts()
  // 反馈提交表单那半篇也一样（lib/feedbackDraft.ts）：它里面可能有这个人的原话、
  // 甚至是他贴进来的一段内部报错。它没有第二个按键让人自己清，只能在这里清。
  forgetFeedbackDraft()
  // 即发即忘：缓存出问题绝不能挡住登录本身。
  if (typeof caches !== 'undefined') void caches.delete(API_CACHE).catch(() => {})
  return true
}

/**
 * 冷打开时这次会话恢复的处境，给界面用。
 *
 * - `restoring`：手里有一份本地会话，正在向服务端确认（访问令牌过期时先换一个）。
 * - `unreachable`：确认不了（网络错误、超时、或者一个不是 401 的失败）。登录**没有**
 *   被否掉，本地会话还在，可以重试。
 *
 * 恢复成功、被服务端明确拒掉（真登出）、或者本来就没登录过时都是 `idle`。
 */
export type RestorePhase = 'idle' | 'restoring' | 'unreachable'

export class AccountService {
  _loggedIn = ref(false)
  _user = ref<User | null>(null)
  _accessToken: string | null = null
  // 见 RestorePhase 的说明。界面据此决定要不要画「正在恢复登录状态 / 连不上、可以
  // 重试」那一层。以前这个信息不存在：弱网冷打开时屏幕上除了空白什么都没有，恢复
  // 失败更是直接把人当生人送走。
  _restorePhase = ref<RestorePhase>('idle')
  // 服务端结束这次登录的动作只做一遍：一次会话结束会从好几个面板各自报上来
  // （`sessionOver`）。登录回来时清掉。
  private endingSession = false
  // 冷打开时的「登没登录」答案。`init` 一发起就把它换成那次恢复的 promise：
  // 手里的访问令牌过期时，恢复要先去换一个（一次网络往返），在那之前 `loggedIn`
  // 是 false 而不是「还不知道」——根路径的守卫要是当场读，就会把回访用户当成
  // 生人送进推广页。要靠登录态做路由决定的地方先 await 这一个。
  sessionRestored: Promise<void> = Promise.resolve()
  // 界面语言存在账号上：推送和桌面端通知是服务端按收件人的语言写的，服务端只能
  // 从这里知道。登录着的时候以账号上那份为准（打开页面、续签都跟着它，别的设备上
  // 改了这里也换过去），浏览器里那份（i18n/index.ts）是它的缓存，也是未登录页面
  // 唯一的一份。新登录那一下例外：这个浏览器里选定过语言，人是看着它登录的，那就
  // 是他眼下的选择，写到账号上；只是跟着浏览器默认语言的，才换成账号上的。
  // 正在保存的那个语言：保存回来之前到达的用户记录（续签、别的标签页）还是旧
  // 的，不能拿它把刚选的语言换回去。
  private savingLanguage: Locale | null = null
  // 这个人自己选过几次语言。取语言包要等一次网络往返，这期间他可能又选了一种：他刚
  // 选的这个比出发时手里那条记录新。已经保存完、`savingLanguage` 又清掉之后才回来的
  // 取包结果，靠这个数挡住。
  private languagePicks = 0
  // 看过多少条用户记录（`followLanguage`）。换语言要等语言包，等到的时候可能已经有
  // 更新的一条记录来过：那次换的是旧记录说的语言，不能落地。
  private followedRecords = 0
  // 时区也存在账号上：安静时段是这个人墙上的钟点（22:00–08:00），服务端按账号上那
  // 份时区算。它跟着他正在用的浏览器走，人到了别的时区，打开页面就跟过去。
  // 正在报的那个时区：报完之前到达的用户记录还是旧的，不能因此再报一遍。
  private reportingTimezone: string | null = null

  constructor() {
    // 续签、登录、退出都可能发生在别的标签页，也可能是这个标签页里别的代码发起
    // 的续签（lib/session.ts）。都从这里接住，内存里这份才不会和存储里的对不上。
    onSessionEvent((event) => {
      if (event.type === 'token') this.adopt(event.token, event.user)
      else if (event.refused && this.loggedIn) void this.sessionOver()
      else void this.forget()
    })
    onLocaleChosen((locale) => void this.languageChosen(locale))
  }

  /** 这个人在界面上选了一种语言（i18n 的 `chooseLocale`，界面已经换过去了）：
   *  登录着就记到账号上。 */
  public async languageChosen(locale: Locale) {
    this.languagePicks += 1
    // 没登录时选择只记在浏览器里，登录那一下再写上去（followLanguage）。
    if (this.loggedIn) await this.saveLanguage(locale)
  }

  private async saveLanguage(locale: Locale) {
    this.savingLanguage = locale
    try {
      await UserApi.setLanguage(locale)
      if (this.user) {
        this.user = { ...this.user, language: locale }
        storageSetJson('user', this.user)
      }
    } catch (error) {
      // 界面已经换过去了；没记上，下次登录或打开页面时会再写一次。
      console.error('Failed to save the language:', error)
    } finally {
      this.savingLanguage = null
    }
  }

  /** 拿到服务端给的用户记录后，让界面和账号上的语言一致。`signingIn`：这条记录
   *  来自一次新登录，而不是续签或打开页面。 */
  private followLanguage(user: User, signingIn = false) {
    // 每一条记录都是眼下最新的一条：换语言要等语言包，等到的时候可能已经有更新的记录
    // 来过，那次换就不能落地了。这条记录自己什么都不换的那两种情况（下面两处 `return`）
    // 也算：记下来的是「这会儿是什么就是什么」，比先前那条记录说的新。
    const record = ++this.followedRecords
    if (this.savingLanguage) return
    const current = i18n.global.locale.value as Locale
    const stored = storedLocale()
    // 浏览器里存着一种语言，只有界面正显示着它时才算「这个人此刻的选择」。首屏那份
    // 语言包取不回来时界面会退回入口那一门而偏好不动（`i18n/index.ts` 的
    // `fallBackToEntryLocale`）：那时把界面语言写回账号，抹掉的是他真正选的那一门。
    // 偏好里存着别的语言就说明界面上这一门不是他选的；账上那条记录说的是这里显示不出来
    // 的一门时（`!isLocale`）也一样，它管不了要哪一门，两边都不写。
    if (!isLocale(user.language) || (signingIn && stored !== null && stored === current)) {
      if (user.language !== current && (stored === null || stored === current)) {
        // 界面这一门写回账号，它就成了账号上那一份：首屏那份语言包迟到落地时不能把它
        // 翻走（见 `noteAccountLocaleSettled`）。
        noteAccountLocaleSettled(current)
        void this.saveLanguage(current)
      }
      return
    }
    if (user.language !== current) {
      // 这份语言包可能还没取过（这台机器一直用的是另一种语言），先取再换：换过去的
      // 界面不该先画出一片裸键。取语言包这段时间里，比这条记录更新的东西可能已经
      // 来了：这个人自己选了一种（`languagePicks`），或者来了更新的记录
      // （`followedRecords`）。都留着他的，不换。（「有人正把眼下这一门记上去」也归这
      // 两条管：那一次写是另一条记录发起的，`followedRecords` 已经比手上这条新。）
      //
      // 取不到就留在这门语言上：没有那份语言包，换过去只会是一片裸键。这一条是后台
      // 的记录跟过来时自己跑的，没有人在等它的结果，失败也不弹东西。（`main.ts` 的
      // 首屏那份同理，它也是等不到就照常挂载。）取包按「没人在等」的那种取
      // （`loadLocale` 的 `unattended`）：这一趟在任何一次导航之外，失败了也留在这门
      // 语言上，拿整页刷新去救它只会一轮一轮地刷。
      const target = user.language
      const picks = this.languagePicks
      void loadLocale(target, true)
        .then(() => {
          if (record === this.followedRecords && this.languagePicks === picks) setLocale(target)
        })
        .catch(() => {})
    } else {
      // 账号上那份就是界面上这一门：不写偏好，也不换。可界面这一门是它定下的：首屏那份
      // 语言包迟到落地时不能把它翻走（见 `noteAccountLocaleSettled`）。
      noteAccountLocaleSettled(current)
    }
  }

  /** 拿到服务端给的用户记录后，账号上的时区和这个浏览器的不一样就报上去。 */
  private followTimezone(user: User) {
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone
    if (!zone || user.timezone === zone || this.reportingTimezone === zone) return
    this.reportingTimezone = zone
    UserApi.setTimezone(zone)
      .then(() => {
        if (this.user) {
          this.user = { ...this.user, timezone: zone }
          storageSetJson('user', this.user)
        }
      })
      // 没报上，下次打开页面会再报一次；在那之前安静时段按账号上原来那份算。
      .catch((error) => console.error('Failed to save the time zone:', error))
      .finally(() => {
        this.reportingTimezone = null
      })
  }

  /** 界面据它决定要不要画「正在恢复登录状态 / 连不上、可以重试」那一层。 */
  public get restorePhase(): RestorePhase {
    return this._restorePhase.value
  }

  public get loggedIn() {
    return this._loggedIn.value
  }

  public set loggedIn(value) {
    this._loggedIn.value = value
  }

  public get user() {
    return this._user.value
  }

  public set user(value) {
    if (value?.id !== this._user.value?.id) identityChanges.value += 1
    this._user.value = value
  }

  public get accessToken() {
    return this._accessToken
  }

  public set accessToken(value) {
    this._accessToken = value
    if (value) {
      storageSet('accessToken', value)
    } else {
      storageRemove('accessToken')
    }
  }

  public async updateUserInfo(signingIn = false) {
    if (!this.loggedIn || !this.accessToken) return

    try {
      const data = await UserApi.getCurrentUser()
      if (data.user) {
        this.user = data.user
        storageSetJson('user', data.user)
        this.followLanguage(data.user, signingIn)
        this.followTimezone(data.user)
      }
    } catch (error) {
      console.error('Failed to update user info:', error)
    }
  }

  public init(): Promise<void> {
    this.sessionRestored = this.restoreSession()
    return this.sessionRestored
  }

  private async restoreSession() {
    const accessToken = storageGet('accessToken')
    const user = storageGetJson<User>('user')
    if (!accessToken || !user) return

    // 访问令牌只活 15 分钟（后端 access_token_expires_seconds），刷新令牌活 30 天。
    // 以前这里不看 exp，直接把 loggedIn 置 true——于是每次进页面（只要距上次活动
    // 超过 15 分钟）都会拿着已经死掉的令牌去打认证接口，
    // /notifications/unread-count 必 401，未读数也就永远停在 0。
    //
    // 页面上绝大多数请求（topics/blocks/projects）在这个部署里根本不鉴权，所以
    // 只有未读数会把坏掉的会话喊出来。
    //
    // 令牌已过期就先续签再宣布登录：等 loggedIn 翻成 true 时手里一定是新令牌，
    // 消费方（useUnreadNotifications watch 了 loggedIn）自然会重新取一次数。
    if (isTokenExpired(accessToken)) {
      this._restorePhase.value = 'restoring'
      // 续签成功才宣布登录——手里没有活令牌，宣布了也只是继续撒 401。
      const outcome = await this.resumeFromCookie()
      if (outcome === 'rejected') {
        // 服务端明确拒了（登录已过期或被撤销）= 真的登出了。先把处境置回去（那一层
        // 立刻收起来），再清会话——退出登录里那几件清理（推送退订、缓存）不该拖着
        // 那一层不放。
        this._restorePhase.value = 'idle'
        await this.forget()
        return
      }
      if (outcome === 'unreachable') {
        // 网络没能确认这次会话。这里不登出（localStorage 照旧保留：这多半是网络
        // 抖动，refresh cookie 还有 30 天），但也不能就此打住——以前那样做的后果是
        // 界面把人当生人送进推广页，会话明明还好好的。把处境记下来，让界面显式说
        // 「连不上、可以重试」，网络回来时再续一次。
        this._restorePhase.value = 'unreachable'
        return
      }
      this._restorePhase.value = 'idle'
      return
    }

    this.accessToken = accessToken
    this.user = user
    this.loggedIn = true
    // 本地那份用户记录已经够做路由决定，恢复到这里就算完成；最新的一份在后台取，
    // 不挡第一页（冷打开时每多等一个往返，骨架屏就多挂一个往返）。取回来跟着变的
    // 东西各自盯着它：缺邮箱的拦截（router/emailRequired.ts）、界面语言、时区。
    void this.updateUserInfo()
  }

  /**
   * 再试一次确认本地会话。界面上的「重试」，以及网络回来 / 标签页回到前台时的
   * 自动重试都走这里。返回这一次之后是不是登上了。
   *
   * 只在 `unreachable` 时有意义：`idle`（没有待恢复的会话）和 `restoring`（已经在
   * 试了）都直接返回现在的登录态。
   */
  public async retryRestore(): Promise<boolean> {
    if (this._restorePhase.value !== 'unreachable') return this.loggedIn
    this._restorePhase.value = 'restoring'
    const outcome = await this.resumeFromCookie()
    if (outcome === 'rejected') {
      // 这一下服务端明确说没有这个会话了——和冷打开那条路一样，真的登出。
      this._restorePhase.value = 'idle'
      await this.forget()
      return false
    }
    this._restorePhase.value = outcome === 'ok' ? 'idle' : 'unreachable'
    return outcome === 'ok'
  }

  /** 人选择先不恢复、以访客身份继续：收起那一层，但本地会话原样留着。 */
  public dismissRestore(): void {
    if (this._restorePhase.value === 'unreachable') this._restorePhase.value = 'idle'
  }

  /**
   * 只凭刷新 cookie 登录：OAuth 回跳落地时，和回访时访问令牌已经过期时。
   *
   * 服务端答复了但拒绝（`rejected`）= 会话真的没了；没拿到答复（`unreachable`）
   * = 别把用户的会话当垫背清掉。
   */
  public async resumeFromCookie(): Promise<'ok' | 'rejected' | 'unreachable'> {
    const outcome = await refreshSession()
    if (outcome.kind !== 'ok') return outcome.kind
    this.adopt(outcome.token, outcome.user)
    if (!this.user) await this.updateUserInfo()
    return 'ok'
  }

  /** 接过一个新令牌：这个标签页续签的，或者别的标签页续签、登录的。 */
  private adopt(accessToken: string, user?: User) {
    this.endingSession = false
    const someoneElse = !!user && dropCachesIfSomeoneElseLogsIn(this.user?.id ?? storedUserId(), user.id)
    this.accessToken = accessToken
    if (user) {
      this.user = user
      storageSetJson('user', user)
    }
    if (someoneElse) readAsNewPerson()
    this.loggedIn = true
    // 手里有活令牌了，恢复这件事就完成了：界面那一层可以收起来。
    this._restorePhase.value = 'idle'
    if (user) {
      this.followLanguage(user)
      this.followTimezone(user)
    }
  }

  public async login(accessToken: string, user?: User) {
    this.endingSession = false
    const someoneElse = dropCachesIfSomeoneElseLogsIn(this.user?.id ?? storedUserId(), user?.id)
    this.loggedIn = true
    this.accessToken = accessToken
    announceSignIn(accessToken, user)

    if (user) {
      // 如果提供了用户信息，直接使用
      this.user = user
      storageSetJson('user', user)
      this.followLanguage(user, true)
      this.followTimezone(user)
    } else {
      // 如果没有提供用户信息（如 OAuth 登录），获取完整的用户信息
      await this.updateUserInfo(true)
    }
    if (someoneElse) readAsNewPerson()
  }

  public async logout() {
    announceSignOut()
    await this.forget()
  }

  /**
   * 服务端把一次正在用的登录结束了（它把续签答成 401）：放下本地会话，告诉人，再把
   * 他送到能重新登录的地方。
   *
   * 顺序不能反过来。`loggedIn` 还是 true 的时候去登录页，`leaveSignInWhenSignedIn`
   * 会把这一跳弹回原来的页面——人看着就是红了一下又回来（`useUserMenu.onLogout` 先
   * await logout 再跳，同一个原因）。而 `forget()` 里那次推送退订要走一个网络往返，
   * 所以放下会话之前先把话说出去，别让人干等着。
   *
   * 这一跳只走一次：好几个面板会各自带着自己的 401 报上来，重来一遍只会多一次退订和
   * 一片重复的提示。
   */
  private async sessionOver() {
    if (this.endingSession) return
    this.endingSession = true
    toast.error(t('shell.errors.sessionExpired'))
    await this.forget()
    // 路由表建好之后才拿得到路由（见 `router/account.ts` 顶上那句）。
    const { default: router } = await import('@/router')
    // 已经在登录页上就不用再跳了；别处跳过去时，`carryLoginRedirect` 会把来路挂上
    // `?redirect=`，登录完回到他刚才那一页。
    if (router.currentRoute.value.name !== 'SignIn') void router.replace({ name: 'SignIn' })
  }

  /** 放下这个标签页里的登录态，不通知别人：别的标签页已经退出，或者会话已经失效。 */
  private async forget() {
    // 先退订推送，趁令牌还在：那一行订阅是按 user_id 存的，留着就等于这台浏览器继
    // 续替上一个人收他的推送 —— 和下面两份缓存同一类问题，只是这一个会主动响。
    // 它自己吞掉所有错误，最坏的后果是后端往一个死地址发几次，投递侧按 404/410
    // 自己删掉。
    await disablePush()
    this.loggedIn = false
    this.user = null
    this._accessToken = null
    // 会话已经没了，没有「待恢复」可言。
    this._restorePhase.value = 'idle'
    storageRemove('accessToken')
    storageRemove('user')
    // Remove the identity cache left by earlier frontend versions.
    storageRemove('cheesex.me')
    // The service-worker API cache is per-browser, not per-user: on a shared
    // machine the next account must not read this one's cached API responses.
    // Drop the data cache on logout (the app-shell precache is not user data,
    // so offline shell loading survives). Fire-and-forget — a cache hiccup must
    // never block sign-out.
    if (typeof caches !== 'undefined') {
      void caches.delete(API_CACHE).catch(() => {})
    }
    // 同理，服务器数据的缓存住在内存里，退出登录不清就还在：下一个人打开总览会先
    // 看到上一个人的项目名，然后才被后台刷新盖掉——那一眼已经泄露了。
    forgetServerData()
    // 反馈那三份同理，而且它们更直接：「我的反馈」和详情装的就是这个人自己那几条。
    resetFeedbackCaches()
    // 输入框草稿同样：它是 localStorage 里的一句半句话，属于上一个人。
    clearComposerDrafts()
    // 反馈提交表单那半篇同理（lib/feedbackDraft.ts）。
    forgetFeedbackDraft()
    // 语言也有一份算在这个人头上：账号上那条记录把界面定在哪一门，是「这个人」的，会话
    // 没了就该跟着没。留着它，首屏那份迟到的语言包会被一条已经不存在的账号记录拦住，
    // 界面就停在退出前那一门上（见 `i18n/index.ts` 的 `forgetAccountLocaleSettled`）。
    forgetAccountLocaleSettled()
  }
}

const accountService = new AccountService()

export default accountService

export const currentUserId = computed(() => accountService.user?.id)

export const currentUserName = computed(() => accountService.user?.username)
