// 当前用户身份。The 知是 login is the ONE sign-in; main.ts mirrors it into the
// `cheesex.me` localStorage entry this module reads, so components can read
// myHandle() once at setup.
import type { Me } from './cx_types'

import { ref } from 'vue'

const KEY = 'cheesex.me'

function load(): Me | null {
  try {
    const raw = localStorage.getItem(KEY)
    if (raw) return JSON.parse(raw) as Me
    // Fresh login in this very session: main.ts's boot mirror hasn't run since
    // sign-in, so synthesize the identity from the 知是 account directly.
    const u = localStorage.getItem('user')
    if (u) {
      const parsed = JSON.parse(u)
      if (parsed?.username) {
        return {
          id: String(parsed.id),
          handle: parsed.username,
          name: parsed.nickname || parsed.username,
          token: '',
        } as Me
      }
    }
    return null
  } catch {
    return null
  }
}

export const me = ref<Me | null>(load())

// The author handle components send with their actions. Retries load() lazily:
// right after a fresh sign-in the module-scope snapshot predates the login, so
// a null/empty identity re-reads localStorage before giving up.
export function myHandle(): string {
  if (!me.value?.handle) me.value = load()
  return me.value?.handle ?? ''
}

// The numeric 知是 user id, for the routes keyed by it (`/users/{id}/oauth/...`).
// Self-heals exactly like `myHandle()`, and for the same reason: sign-in is an
// SPA `router.replace`, never a reload, so this module's boot-time snapshot is
// still the logged-OUT one. Reading `me.value?.id` raw made the 连接 GitHub 账号
// section render its 「未登录」 error branch to a user who had just logged in.
export function myId(): string {
  if (!me.value?.id) me.value = load()
  return me.value?.id ?? ''
}

// The signed session token (P1 真鉴权). api.ts attaches it as a Bearer header;
// empty when signed out or when an older (pre-token) identity is cached.
export function authToken(): string {
  return me.value?.token ?? ''
}
