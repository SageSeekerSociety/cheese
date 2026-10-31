// 项目清单在本标签页里存一份：冷打开（刷新页面）时左边栏的项目格子当场就有，不等
// 那一次读。频道清单、成员和最近去过的房间另存在 IndexedDB 里，见 `persistRooms`。
//
// 存在 sessionStorage：关掉标签页就没了，同一台机器上的下一个人拿不到。换了人登录
// 时整份清掉（`forgetPersistedQueries`，见 services/account.ts）。
import type { PersistedClient, Persister } from '@tanstack/query-persist-client-core'

import { persistQueryClient } from '@tanstack/query-persist-client-core'

import { rememberProjects } from '@/lib/addresses'
import { storageGetJson, storageRemove, storageSetJson } from '@/lib/browserStorage'
import { peekEarlyProjects } from '@/lib/earlyProjects'
import { myHandle } from '@/me'
import { queryClient } from '@/query/client'
import { keys } from '@/query/keys'
import { forgetRoomCache } from '@/query/persistRooms'

const STORAGE_KEY = 'cheesex.queries.v1'
const MAX_AGE_MS = 24 * 60 * 60 * 1000

const persister: Persister = {
  persistClient(client: PersistedClient) {
    // 写不进去（存储被禁用或写满）就算了：内存里那份照样能用。
    storageSetJson(STORAGE_KEY, client, 'session')
  },
  restoreClient() {
    return storageGetJson<PersistedClient>(STORAGE_KEY, 'session') ?? undefined
  },
  removeClient() {
    storageRemove(STORAGE_KEY, 'session')
  },
}

/**
 * 存着的那份清单里，「短名 → 项目」的对照当场记下来。
 *
 * 冷开一个项目页时，地址守卫（`lib/addresses` 的 `canonicalAddress`）要先把短名换成
 * UUID 才让页面的代码下去；换不出来就得先等一次 `/addresses` 请求。存着的清单里本来
 * 就有这份对照，先记下来，守卫那一步就不必再等一个来回。
 *
 * 只记对照，不放缓存：缓存里那份旧清单照旧由 `persistQueryClient` 放回来，页面照旧
 * 重读一次。换了人（`buster` 对不上）或者存得太久的一律不看。
 */
function seedKnownProjects(): void {
  // 读不动（没存过、坏了、存储被禁用）就是没有：后面照常去读。
  const stored = storageGetJson<PersistedClient>(STORAGE_KEY, 'session')
  if (!stored) return
  if (stored.buster !== myHandle()) return
  if (Date.now() - stored.timestamp > MAX_AGE_MS) return
  try {
    for (const query of stored.clientState?.queries ?? []) {
      if (query.queryKey?.[0] !== keys.projects()[0]) continue
      const rows = query.state?.data
      if (Array.isArray(rows)) rememberProjects(rows as { id: string; slug?: string }[])
    }
  } catch {
    // 模块只保证「读不抛」，不管存进去的**形状**（判据 2）——形状不对的这份跳过就好，
    // 不拖累启动。
  }
}

/** 打开页面时调一次：先把存着的那份放回缓存，之后清单一变就存一份。 */
export function persistProjectList(): void {
  seedKnownProjects()
  void peekEarlyProjects().then((payload) => {
    if (payload) rememberProjects(payload.data)
  })
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
  forgetRoomCache()
}
