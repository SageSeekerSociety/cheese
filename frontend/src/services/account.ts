import type { User } from '@/types/users'

import { computed, ref } from 'vue'

import { clearPageCache } from '@/lib/pageCache'
import { UserApi } from '@/network/api/users'
import { BusinessError } from '@/network/types/error'

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

export class AccountService {
  _loggedIn = ref(false)
  _user = ref<User | null>(null)
  _accessToken: string | null = null

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
    this._user.value = value
  }

  public get accessToken() {
    return this._accessToken
  }

  public set accessToken(value) {
    this._accessToken = value
    if (value) {
      localStorage.setItem('accessToken', value)
    } else {
      localStorage.removeItem('accessToken')
    }
  }

  public async updateUserInfo() {
    if (!this.loggedIn || !this.accessToken) return

    try {
      const { data } = await UserApi.getCurrentUser()
      if (data.user) {
        this.user = data.user
        localStorage.setItem('user', JSON.stringify(data.user))
      }
    } catch (error) {
      console.error('Failed to update user info:', error)
    }
  }

  public async init() {
    const accessToken = localStorage.getItem('accessToken')
    const user = localStorage.getItem('user')
    if (!accessToken || !user) return

    // 访问令牌只活 15 分钟（后端 access_token_expires_seconds），刷新令牌活 30 天。
    // 以前这里不看 exp，直接把 loggedIn 置 true——于是每次进页面（只要距上次活动
    // 超过 15 分钟）都会拿着已经死掉的令牌去打认证接口，铃铛的
    // /notifications/unread-count 必 401，未读数也就永远停在 0。
    //
    // 页面上绝大多数请求（topics/blocks/projects）在这个部署里根本不鉴权，所以
    // 只有铃铛会把坏掉的会话喊出来——看着像"铃铛坏了"，其实是"只有铃铛说实话"。
    //
    // 令牌已过期就先续签再宣布登录：等 loggedIn 翻成 true 时手里一定是新令牌，
    // 消费方（AppBar / useNotifications 都 watch 了 loggedIn）自然会重新取一次数。
    if (isTokenExpired(accessToken)) {
      // 无论如何都不宣布登录——手里没有活令牌，宣布了也只是继续撒 401。
      // 区别只在要不要连本地那份会话一起清掉：
      const outcome = await this.refreshExpiredSession()
      if (outcome === 'rejected') {
        // 服务端明确拒了（刷新令牌过期/被撤销）= 真的登出了。
        await this.logout()
      }
      // outcome === 'unreachable' 时故意留着 localStorage：这多半是网络抖动，
      // 刷新令牌还有 30 天，下次进页面再续一次就好，没必要逼用户重新登录。
      return
    }

    this.accessToken = accessToken
    this.user = JSON.parse(user)
    this.loggedIn = true
    // 初始化完成后更新用户信息
    await this.updateUserInfo()
  }

  /**
   * 用 httpOnly 的 REFRESH_TOKEN cookie 换一个新的访问令牌。成功才登录。
   *
   * 失败分两种，处理方式不同：服务端答复了但拒绝（`rejected`）= 会话真的没了；
   * 压根没拿到答复（`unreachable`，网络错误）= 别把用户的会话当垫背清掉。
   */
  private async refreshExpiredSession(): Promise<'ok' | 'rejected' | 'unreachable'> {
    try {
      const { data } = await UserApi.refreshAccessToken()
      if (!data?.accessToken || !data?.user) return 'rejected'
      await this.login(data.accessToken, data.user)
      return 'ok'
    } catch (error) {
      // 拦截器给「有响应」的错误统一包成 BusinessError（ServerError 是它的子类）
      // 并带上 code；网络错误则是一个裸 Error。
      return error instanceof BusinessError ? 'rejected' : 'unreachable'
    }
  }

  public async login(accessToken: string, user?: User) {
    this.loggedIn = true
    this.accessToken = accessToken
    localStorage.setItem('accessToken', accessToken)

    if (user) {
      // 如果提供了用户信息，直接使用
      this.user = user
      localStorage.setItem('user', JSON.stringify(user))
    } else {
      // 如果没有提供用户信息（如 OAuth 登录），获取完整的用户信息
      await this.updateUserInfo()
    }
  }

  public async logout() {
    this.loggedIn = false
    this.user = null
    this._accessToken = null
    localStorage.removeItem('accessToken')
    localStorage.removeItem('user')
    // Drop the cheesex identity mirror too: main.ts only rebuilds it when it's
    // missing, so leaving it here makes the NEXT login keep the previous
    // account's handle — requests then carry new token + old handle and 403.
    localStorage.removeItem('cheesex.me')
    // The service-worker API cache is per-browser, not per-user: on a shared
    // machine the next account must not read this one's cached API responses.
    // Drop the data cache on logout (the app-shell precache is not user data,
    // so offline shell loading survives). Fire-and-forget — a cache hiccup must
    // never block sign-out.
    if (typeof caches !== 'undefined') {
      void caches.delete('cheese-api-get').catch(() => {})
    }
    // 同理，页面缓存住在内存里，退出登录不清就还在：下一个人打开总览会先看到上
    // 一个人的项目名，然后才被后台刷新盖掉——那一眼已经泄露了。
    clearPageCache()
  }
}

const accountService = new AccountService()

export default accountService

export const currentUserId = computed(() => accountService.user?.id)

export const currentUserName = computed(() => accountService.user?.username)
