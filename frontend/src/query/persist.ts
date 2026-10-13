// 项目清单在本标签页里存一份：冷打开（刷新页面）时左边栏的项目格子当场就有，不等
// 那一次读。只存这一份 —— 别的数据打开页面时本来就要重读，存下来只会先画一份旧的。
//
// 存在 sessionStorage：关掉标签页就没了，同一台机器上的下一个人拿不到。换了人登录
// 时整份清掉（`forgetPersistedQueries`，见 services/account.ts）。
import type { PersistedClient, Persister } from '@tanstack/query-persist-client-core'

import { persistQueryClient } from '@tanstack/query-persist-client-core'

import { myHandle } from '@/me'
import { queryClient } from '@/query/client'
import { keys } from '@/query/keys'

const STORAGE_KEY = 'cheesex.queries.v1'
const MAX_AGE_MS = 24 * 60 * 60 * 1000

function storage(): Storage | null {
  try {
    return typeof sessionStorage === 'undefined' ? null : sessionStorage
  } catch {
    return null
  }
}

const persister: Persister = {
  persistClient(client: PersistedClient) {
    try {
      storage()?.setItem(STORAGE_KEY, JSON.stringify(client))
    } catch {
      // 存储被禁用或写满：内存里那份照样能用。
    }
  },
  restoreClient() {
    try {
      const raw = storage()?.getItem(STORAGE_KEY)
      return raw ? (JSON.parse(raw) as PersistedClient) : undefined
    } catch {
      return undefined
    }
  },
  removeClient() {
    try {
      storage()?.removeItem(STORAGE_KEY)
    } catch {
      // 同上。
    }
  },
}

/** 打开页面时调一次：先把存着的那份放回缓存，之后清单一变就存一份。 */
export function persistProjectList(): void {
  persistQueryClient({
    queryClient,
    persister,
    maxAge: MAX_AGE_MS,
    // 存的是谁的清单：换了人，上一个人那份不放回来。
    buster: myHandle(),
    dehydrateOptions: {
      shouldDehydrateQuery: (query) => query.queryKey[0] === keys.projects()[0] && query.state.status === 'success',
    },
  })
}

export function forgetPersistedQueries(): void {
  void persister.removeClient()
}
